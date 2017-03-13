#!/usr/bin/env python
import argparse
import config
import datetime
import github3
import json
import logging
import mozreview
import os
import psycopg2
import random
import re
import requests
import shlex
import subprocess
import sys
import time
from . import utils
from .git_helper import SSH_KEY_FILE
from contextlib import contextmanager
from enum import IntEnum
from itertools import chain
from queue import Queue
import traceback

sys.path.insert(0, os.path.normpath(os.path.join(os.path.normpath(
                os.path.abspath(os.path.dirname(__file__))), '..',
                                                             '..',
                                                             'pylib',
                                                             'mozautomation')))
import transplant
import treestatus

# Servo lookup table for sorting PRs
STATUS_TO_PRIORITY = {
    'success': 0,
    'pending': 1,
    'approved': 2,
    '': 3,
    'error': 4,
    'failure': 5,
}

# Messages for Buildbot comments. Remove if Buildbot goes away.
INTERRUPTED_BY_AUTOLAND_FMT = 'Interrupted by Autoland ({})'
INTERRUPTED_BY_AUTOLAND_RE = re.compile(r'Interrupted by Autoland \((.+?)\)')

# Delay before giving up on retrying a PR
TEST_TIMEOUT = 3600 * 10

# Cute debugging messages came in with Homu
PORTAL_TURRET_DIALOG = ["Target acquired", "Activated", "Who's there?", "There you are"]
PORTAL_TURRET_IMAGE = "https://cloud.githubusercontent.com/assets/1617736/22222924/c07b2a1c-e16d-11e6-91b3-ac659550585c.png"

# HG max attempts to transplant before bailing
MAX_TRANSPLANT_ATTEMPTS = 50

# max updates to post to reviewboard / iteration
MOZREVIEW_COMMENT_LIMIT = 10

# time to wait before attempting to update MozReview after a failure to post
MOZREVIEW_RETRY_DELAY = datetime.timedelta(minutes=5)

# time to wait before retrying a transplant
TRANSPLANT_RETRY_DELAY = datetime.timedelta(minutes=5)


def handle_pending_transplants(logger, dbconn):
    cursor = dbconn.cursor()
    now = datetime.datetime.now()
    query = """
        SELECT id, destination, request
        FROM Transplant
        WHERE landed IS NULL
              AND (last_updated IS NULL OR last_updated<=%(time)s)
        ORDER BY created
    """
    transplant_retry_delay = TRANSPLANT_RETRY_DELAY
    if config.testing():
        transplant_retry_delay = datetime.timedelta(seconds=1)

    cursor.execute(query, ({'time': now - transplant_retry_delay}))

    current_treestatus = {}
    finished_revisions = []
    mozreview_updates = []
    retry_revisions = []

    def handle_treeclosed(transplant_id, tree, rev, destination, trysyntax,
                          pingback_url):
        retry_revisions.append((now, transplant_id))

        data = {
            'request_id': transplant_id,
            'tree': tree,
            'rev': rev,
            'destination': destination,
            'trysyntax': trysyntax,
            'landed': False,
            'error_msg': '',
            'result': 'Tree %s is closed - retrying later.' % tree
        }
        mozreview_updates.append([transplant_id, json.dumps(data)])

    # This code is a bit messy because we have to deal with the fact that the
    # the tree could close between the call to tree_is_open and when we
    # actually attempt the revision.
    #
    # We keep a list of revisions to retry called retry_revisions which we
    # append to whenever we detect a closed tree. These revisions have their
    # last_updated field updated so we will retry them after a suitable delay.
    #
    # The other list we keep is for transplant attempts that either succeeded
    # or failed due to a reason other than a closed tree, which is called
    # finished_revisions. Successful or not, we're finished with them, they
    # will not be retried.
    for row in cursor.fetchall():
        transplant_id, destination, request = row

        # Many of these values are used as command arguments. So convert
        # to binary because command arguments aren't unicode.
        destination = destination.encode('ascii')
        requester = request['ldap_username']
        tree = request['tree'].encode('ascii')
        rev = request['rev'].encode('ascii')
        trysyntax = request.get('trysyntax', '')
        push_bookmark = request.get('push_bookmark', '').encode('ascii')
        pingback_url = request.get('pingback_url', '').encode('ascii')
        commit_descriptions = request.get('commit_descriptions')
        tree_open = current_treestatus.setdefault(
            destination, treestatus.tree_is_open(logger, destination))

        if not tree_open:
            handle_treeclosed(transplant_id, tree, rev, destination,
                              trysyntax, pingback_url)
            continue

        attempts = 0
        started = datetime.datetime.now()
        while attempts < MAX_TRANSPLANT_ATTEMPTS:
            logger.info('initiating transplant from tree: %s rev: %s '
                        'to destination: %s, attempt %s' % (
                            tree, rev, destination, attempts + 1))

            # TODO: We should break the transplant call into two steps, one
            #       to pull down the commits to transplant, and another
            #       one to rebase it and attempt to push so we don't
            #       duplicate work unnecessarily if we have to rebase more
            #       than once.
            os.environ['AUTOLAND_REQUEST_USER'] = requester
            landed, result = transplant.transplant(logger, tree,
                                                   destination, rev,
                                                   trysyntax, push_bookmark,
                                                   commit_descriptions)
            del os.environ['AUTOLAND_REQUEST_USER']

            logger.info('transplant from tree: %s rev: %s attempt: %s: %s' % (
                tree, rev, attempts + 1, result))

            if landed or 'abort: push creates new remote head' not in result:
                break

            attempts += 1

        if landed:
            logger.info('transplant successful - new revision: %s' % result)
        else:
            if 'is CLOSED!' in result:
                logger.info('transplant failed: tree: %s is closed - '
                            ' retrying later.' % tree)
                current_treestatus[destination] = False
                handle_treeclosed(transplant_id, tree, rev, destination,
                                  trysyntax, pingback_url)
                continue
            elif 'abort: push creates new remote head' in result:
                logger.info('transplant failed: we lost a push race')
                retry_revisions.append((now, transplant_id))
                continue
            elif 'unresolved conflicts (see hg resolve' in result:
                logger.info('transplant failed - manual rebase required: '
                            'tree: %s rev: %s destination: %s error: %s' %
                            (tree, rev, destination, result))
                # This is the only autoland error for which we expect the
                # user to take action. We should make things nicer than the
                # raw mercurial error.
                # TODO: sad trombone sound
                header = ('We\'re sorry, Autoland could not rebase your '
                          'commits for you automatically. Please manually '
                          'rebase your commits and try again.\n\n')
                result = header + result
            else:
                logger.info('transplant failed: tree: %s rev: %s '
                            'destination: %s error: %s' %
                            (tree, rev, destination, result))

        completed = datetime.datetime.now()
        logger.info('elapsed transplant time: %s' % (completed - started))

        # set up data to be posted back to mozreview
        data = {
            'request_id': transplant_id,
            'tree': tree,
            'rev': rev,
            'destination': destination,
            'trysyntax': trysyntax,
            'landed': landed,
            'error_msg': '',
            'result': ''
        }

        if landed:
            data['result'] = result
        else:
            data['error_msg'] = result

        mozreview_updates.append([transplant_id, json.dumps(data)])

        finished_revisions.append([landed, result, transplant_id])

    if retry_revisions:
        query = """
            update Transplant set last_updated=%s
            where id=%s
        """
        cursor.executemany(query, retry_revisions)
        dbconn.commit()

    if finished_revisions:
        query = """
            update Transplant set landed=%s,result=%s
            where id=%s
        """
        cursor.executemany(query, finished_revisions)
        dbconn.commit()

    if mozreview_updates:
        query = """
            insert into MozreviewUpdate(transplant_id,data)
            values(%s,%s)
        """
        cursor.executemany(query, mozreview_updates)
        dbconn.commit()


def handle_pending_mozreview_updates(logger, dbconn):
    """Attempt to post updates to mozreview"""

    cursor = dbconn.cursor()
    query = """
        select MozreviewUpdate.id,transplant_id,request,data
        from MozreviewUpdate inner join Transplant
        on (Transplant.id = MozreviewUpdate.transplant_id)
        limit %(limit)s
    """
    cursor.execute(query, {'limit': MOZREVIEW_COMMENT_LIMIT})

    bugzilla_auth = mozreview.instantiate_authentication()

    updated = []
    all_posted = True
    for row in cursor.fetchall():
        update_id, transplant_id, request, data = row
        pingback_url = request.get('pingback_url')

        logger.info('trying to post mozreview update to: %s for request: %s' %
                    (pingback_url, transplant_id))

        # We allow empty pingback_urls as they make testing easier. We can
        # always check the logs for misconfigured pingback_urls.
        if pingback_url:
            status_code, text = mozreview.update_review(bugzilla_auth,
                                                        pingback_url, data)
            if status_code == 200:
                updated.append([update_id])
            else:
                logger.info('failed: %s - %s' % (status_code, text))
                all_posted = False
                break
        else:
            updated.append([update_id])

    if updated:
        query = """
            delete from MozreviewUpdate
            where id=%s
        """
        cursor.executemany(query, updated)
        dbconn.commit()

    return all_posted


def get_dbconn(dsn):
    dbconn = None
    while not dbconn:
        try:
            dbconn = psycopg2.connect(dsn)
        except psycopg2.OperationalError:
            time.sleep(0.1)
    return dbconn


def db_query(db, *args):
    # FIXME: Please check this; I'm not fluent in postgres
    cursor = db.cursor()
    cursor.execute(*args)
    db.commit()


@contextmanager
def buildbot_sess(repo_cfg):
    sess = requests.Session()

    sess.post(repo_cfg['buildbot']['url'] + '/login', allow_redirects=False, data={
        'username': repo_cfg['buildbot']['username'],
        'passwd': repo_cfg['buildbot']['password'],
    })

    yield sess

    sess.get(repo_cfg['buildbot']['url'] + '/logout', allow_redirects=False)


class Repository:
    treeclosed = -1
    gh = None
    label = None
    dbconn = None

    def __init__(self, gh, repo_label, dbconn):
        self.gh = gh
        self.repo_label = repo_label
        self.db = dbconn
        db_query(dbconn, 'SELECT treeclosed FROM repos WHERE repo = ?', [repo_label])
        # FIXME this may be incorrect use of database
        row = dbconn.fetchone()
        if row:
            self.treeclosed = row[0]
        else:
            self.treeclosed = -1

    def update_treeclosed(self, value):
        self.treeclosed = value
        db_query(self.db, 'DELETE FROM repos where repo = ?', [self.repo_label])
        if value > 0:
            db_query(self.db, 'INSERT INTO repos (repo, treeclosed) VALUES (?, ?)', [self.repo_label, value])

    def __lt__(self, other):
        return self.gh < other.gh


class PullReqState:
    num = 0
    priority = 0
    rollup = False
    title = ''
    body = ''
    head_ref = ''
    base_ref = ''
    assignee = ''
    delegate = ''

    def __init__(self, num, head_sha, status, db, repo_label, mergeable_que, gh, owner, name, repos):
        self.head_advanced('', use_db=False)

        self.num = num
        self.head_sha = head_sha
        self.status = status
        self.db = db
        self.repo_label = repo_label
        self.mergeable_que = mergeable_que
        self.gh = gh
        self.owner = owner
        self.name = name
        self.repos = repos
        self.test_started = time.time()  # FIXME: Save in the local database

    def head_advanced(self, head_sha, *, use_db=True):
        self.head_sha = head_sha
        self.approved_by = ''
        self.status = ''
        self.merge_sha = ''
        self.build_res = {}
        self.try_ = False
        self.mergeable = None

        if use_db:
            self.set_status('')
            self.set_mergeable(None)
            self.init_build_res([])

    def __repr__(self):
        return 'PullReqState:{}/{}#{}(approved_by={}, priority={}, status={})'.format(
            self.owner,
            self.name,
            self.num,
            self.approved_by,
            self.priority,
            self.status,
        )

    def sort_key(self):
        return [
            STATUS_TO_PRIORITY.get(self.get_status(), -1),
            1 if self.mergeable is False else 0,
            0 if self.approved_by else 1,
            1 if self.rollup else 0,
            -self.priority,
            self.num,
        ]

    def __lt__(self, other):
        return self.sort_key() < other.sort_key()

    def get_issue(self):
        issue = getattr(self, 'issue', None)
        if not issue:
            issue = self.issue = self.get_repo().issue(self.num)
        return issue

    def add_comment(self, text):
        self.get_issue().create_comment(text)

    def set_status(self, status):
        self.status = status

        db_query(self.db, 'UPDATE pull SET status = ? WHERE repo = ? AND num = ?', [self.status, self.repo_label, self.num])

        # FIXME: self.try_ should also be saved in the database
        if not self.try_:
            db_query(self.db, 'UPDATE pull SET merge_sha = ? WHERE repo = ? AND num = ?', [self.merge_sha, self.repo_label, self.num])

    def get_status(self):
        return 'approved' if self.status == '' and self.approved_by and self.mergeable is not False else self.status

    def set_mergeable(self, mergeable, *, cause=None, que=True):
        if mergeable is not None:
            self.mergeable = mergeable

            db_query(self.db, 'INSERT OR REPLACE INTO mergeable (repo, num, mergeable) VALUES (?, ?, ?)', [self.repo_label, self.num, self.mergeable])
        else:
            if que:
                self.mergeable_que.put([self, cause])
            else:
                self.mergeable = None

            db_query(self.db, 'DELETE FROM mergeable WHERE repo = ? AND num = ?', [self.repo_label, self.num])

    def init_build_res(self, builders, *, use_db=True):
        self.build_res = {x: {
            'res': None,
            'url': '',
        } for x in builders}

        if use_db:
            db_query(self.db, 'DELETE FROM build_res WHERE repo = ? AND num = ?', [self.repo_label, self.num])

    def set_build_res(self, builder, res, url):
        if builder not in self.build_res:
            raise Exception('Invalid builder: {}'.format(builder))

        self.build_res[builder] = {
            'res': res,
            'url': url,
        }

        db_query(self.db, 'INSERT OR REPLACE INTO build_res (repo, num, builder, res, url, merge_sha) VALUES (?, ?, ?, ?, ?, ?)', [
            self.repo_label,
            self.num,
            builder,
            res,
            url,
            self.merge_sha,
        ])

    def build_res_summary(self):
        return ', '.join('{}: {}'.format(builder, data['res'])
                         for builder, data in self.build_res.items())

    def get_repo(self):
        repo = self.repos[self.repo_label].gh
        if not repo:
            self.repos[self.repo_label].gh = repo = self.gh.repository(self.owner, self.name)

            assert repo.owner.login == self.owner
            assert repo.name == self.name
        return repo

    def save(self):
        db_query(self.db, 'INSERT OR REPLACE INTO pull (repo, num, status, merge_sha, title, body, head_sha, head_ref, base_ref, assignee, approved_by, priority, try_, rollup, delegate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            self.repo_label,
            self.num,
            self.status,
            self.merge_sha,
            self.title,
            self.body,
            self.head_sha,
            self.head_ref,
            self.base_ref,
            self.assignee,
            self.approved_by,
            self.priority,
            self.try_,
            self.rollup,
            self.delegate,
        ])

    def refresh(self):
        issue = self.get_repo().issue(self.num)

        self.title = issue.title
        self.body = issue.body

    def fake_merge(self, repo_cfg):
        if not repo_cfg.get('linear', False) or repo_cfg.get('autosquash', False):
            return

        issue = self.get_issue()
        title = issue.title
        # We tell github to close the PR via the commit message, but it doesn't know that
        # constitutes a merge.  Edit the title so that it's clearer.
        merged_prefix = '[merged] '
        if not title.startswith(merged_prefix):
            title = merged_prefix + title
            issue.edit(title=title)

    def change_treeclosed(self, value):
        self.repos[self.repo_label].update_treeclosed(value)

    def blocked_by_closed_tree(self):
        treeclosed = self.repos[self.repo_label].treeclosed
        return treeclosed if self.priority < treeclosed else None


class AuthState(IntEnum):
    # Higher is more privileged
    REVIEWER = 3
    TRY = 2
    NONE = 1


def verify_auth(username, repo_cfg, state, auth, realtime, my_username):
    # In some cases (e.g. non-fully-qualified r+) we recursively talk to ourself
    # via a hidden markdown comment in the message. This is so that
    # when re-synchronizing after shutdown we can parse these comments
    # and still know the SHA for the approval.
    #
    # So comments from self should always be allowed
    if username == my_username:
        return True
    is_reviewer = False
    auth_collaborators = repo_cfg.get('auth_collaborators', False)
    if auth_collaborators:
        is_reviewer = state.get_repo().is_collaborator(username)
    if not is_reviewer:
        is_reviewer = username in repo_cfg.get('reviewers', [])
    if not is_reviewer:
        is_reviewer = username.lower() == state.delegate.lower()

    if is_reviewer:
        have_auth = AuthState.REVIEWER
    elif username in repo_cfg.get('try_users', []):
        have_auth = AuthState.TRY
    else:
        have_auth = AuthState.NONE
    if have_auth >= auth:
        return True
    else:
        if realtime:
            reply = '@{}: :key: Insufficient privileges: '.format(username)
            if auth == AuthState.REVIEWER:
                if auth_collaborators:
                    reply += 'Collaborator required'
                else:
                    reply += 'Not in reviewers'
            elif auth == AuthState.TRY:
                    reply += 'and not in try users'
            state.add_comment(reply)
        return False


def parse_commands(body, username, repo_cfg, state, my_username, db, states, *, realtime=False, sha=''):

    state_changed = False

    words = list(chain.from_iterable(re.findall(r'\S+', x) for x in body.splitlines() if '@' + my_username in x))
    if words[1:] == ["are", "you", "still", "there?"] and realtime:
        state.add_comment(":cake: {}\n\n![]({})".format(random.choice(PORTAL_TURRET_DIALOG), PORTAL_TURRET_IMAGE))
    for i, word in reversed(list(enumerate(words))):
        found = True
        if word == 'r+' or word.startswith('r='):
            if not verify_auth(username, repo_cfg, state, AuthState.REVIEWER, realtime, my_username):
                continue

            if not sha and i + 1 < len(words):
                cur_sha = utils.sha_or_blank(words[i + 1])
            else:
                cur_sha = sha

            approver = word[len('r='):] if word.startswith('r=') else username

            # Ignore "r=me"
            if approver == 'me':
                continue

            # Ignore WIP PRs
            if any(map(state.title.startswith, [
                'WIP', 'TODO', '[WIP]', '[TODO]',
            ])):
                if realtime:
                    state.add_comment(':clipboard: Looks like this PR is still in progress, ignoring approval')
                continue

            # Sometimes, GitHub sends the head SHA of a PR as 0000000 through the webhook. This is
            # called a "null commit", and seems to happen when GitHub internally encounters a race
            # condition. Last time, it happened when squashing commits in a PR. In this case, we
            # just try to retrieve the head SHA manually.
            if all(x == '0' for x in state.head_sha):
                if realtime:
                    state.add_comment(':bangbang: Invalid head SHA found, retrying: `{}`'.format(state.head_sha))

                state.head_sha = state.get_repo().pull_request(state.num).head.sha
                state.save()

                assert any(x != '0' for x in state.head_sha)

            if state.approved_by and realtime and username != my_username:
                for _state in states[state.repo_label].values():
                    if _state.status == 'pending':
                        break
                else:
                    _state = None

                lines = []

                if state.status in ['failure', 'error']:
                    lines.append('- This pull request previously failed. You should add more commits to fix the bug, or use `retry` to trigger a build again.')

                if _state:
                    if state == _state:
                        lines.append('- This pull request is currently being tested. If there\'s no response from the continuous integration service, you may use `retry` to trigger a build again.')
                    else:
                        lines.append('- There\'s another pull request that is currently being tested, blocking this pull request: #{}'.format(_state.num))

                if lines:
                    lines.insert(0, '')
                lines.insert(0, ':bulb: This pull request was already approved, no need to approve it again.')

                state.add_comment('\n'.join(lines))

            if utils.sha_cmp(cur_sha, state.head_sha):
                state.approved_by = approver
                state.try_ = False
                state.set_status('')

                state.save()
            elif realtime and username != my_username:
                if cur_sha:
                    msg = '`{}` is not a valid commit SHA.'.format(cur_sha)
                    state.add_comment(':scream_cat: {} Please try again with `{:.7}`.'.format(msg, state.head_sha))
                else:
                    state.add_comment(':pushpin: Commit {:.7} has been approved by `{}`\n\n<!-- @{} r={} {} -->'.format(state.head_sha, approver, my_username, approver, state.head_sha))
                    treeclosed = state.blocked_by_closed_tree()
                    if treeclosed:
                        state.add_comment(':evergreen_tree: The tree is currently closed for pull requests below priority {}, this pull request will be tested once the tree is reopened'.format(treeclosed))

        elif word == 'r-':
            if not verify_auth(username, repo_cfg, state, AuthState.REVIEWER, realtime, my_username):
                continue

            state.approved_by = ''

            state.save()

        elif word.startswith('p='):
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            try:
                state.priority = int(word[len('p='):])
            except ValueError:
                pass

            state.save()

        elif word.startswith('delegate='):
            if not verify_auth(username, repo_cfg, state, AuthState.REVIEWER, realtime, my_username):
                continue

            state.delegate = word[len('delegate='):]
            state.save()

            if realtime:
                state.add_comment(':v: @{} can now approve this pull request'.format(state.delegate))

        elif word == 'delegate-':
            # TODO: why is this a TRY?
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            state.delegate = ''
            state.save()

        elif word == 'delegate+':
            if not verify_auth(username, repo_cfg, state, AuthState.REVIEWER, realtime, my_username):
                continue

            state.delegate = state.get_repo().pull_request(state.num).user.login
            state.save()

            if realtime:
                state.add_comment(':v: @{} can now approve this pull request'.format(state.delegate))

        elif word == 'retry' and realtime:
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            state.set_status('')

        elif word in ['try', 'try-'] and realtime:
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            state.try_ = word == 'try'

            state.merge_sha = ''
            state.init_build_res([])

            state.save()

        elif word in ['rollup', 'rollup-']:
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            state.rollup = word == 'rollup'

            state.save()

        elif word == 'force' and realtime:
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            if 'buildbot' in repo_cfg:
                with buildbot_sess(repo_cfg) as sess:
                    res = sess.post(repo_cfg['buildbot']['url'] + '/builders/_selected/stopselected', allow_redirects=False, data={
                        'selected': repo_cfg['buildbot']['builders'],
                        'comments': INTERRUPTED_BY_AUTOLAND_FMT.format(int(time.time())),
                    })

            if 'authzfail' in res.text:
                err = 'Authorization failed'
            else:
                mat = re.search('(?s)<div class="error">(.*?)</div>', res.text)
                if mat:
                    err = mat.group(1).strip()
                    if not err:
                        err = 'Unknown error'
                else:
                    err = ''

            if err:
                state.add_comment(':bomb: Buildbot returned an error: `{}`'.format(err))

        elif word == 'clean' and realtime:
            if not verify_auth(username, repo_cfg, state, AuthState.TRY, realtime, my_username):
                continue
            state.merge_sha = ''
            state.init_build_res([])

            state.save()
        elif (word == 'hello?' or word == 'ping') and realtime:
            state.add_comment(":sleepy: I'm awake I'm awake")
        elif word.startswith('treeclosed='):
            if not verify_auth(username, repo_cfg, state, AuthState.REVIEWER, realtime, my_username):
                continue
            try:
                treeclosed = int(word[len('treeclosed='):])
                state.change_treeclosed(treeclosed)
            except ValueError:
                pass
            state.save()
        elif word == 'treeclosed-':
            if not verify_auth(username, repo_cfg, state, AuthState.REVIEWER, realtime, my_username):
                continue
            state.change_treeclosed(-1)
            state.save()
        else:
            found = False

        if found:
            state_changed = True

            words[i] = ''

    return state_changed


def git_push(git_cmd, branch, state):
    merge_sha = subprocess.check_output(git_cmd('rev-parse', 'HEAD')).decode('ascii').strip()

    if utils.silent_call(git_cmd('push', '-f', 'origin', branch)):
        utils.logged_call(git_cmd('branch', '-f', 'homu-tmp', branch))
        utils.logged_call(git_cmd('push', '-f', 'origin', 'homu-tmp'))

        def inner():
            utils.github_create_status(state.get_repo(), merge_sha, 'success', '', 'Branch protection bypassed', context='homu')

        def fail(err):
            state.add_comment(':boom: Unable to create a status for {} ({})'.format(merge_sha, err))

        utils.retry_until(inner, fail, state)

        utils.logged_call(git_cmd('push', '-f', 'origin', branch))

    return merge_sha


def init_local_git_cmds(repo_cfg, git_cfg):
    fpath = 'cache/{}/{}'.format(repo_cfg['owner'], repo_cfg['name'])
    url = 'git@github.com:{}/{}.git'.format(repo_cfg['owner'], repo_cfg['name'])

    if not os.path.exists(SSH_KEY_FILE):
        os.makedirs(os.path.dirname(SSH_KEY_FILE), exist_ok=True)
        with open(SSH_KEY_FILE, 'w') as fp:
            fp.write(git_cfg['ssh_key'])
        os.chmod(SSH_KEY_FILE, 0o600)

    if not os.path.exists(fpath):
        utils.logged_call(['git', 'init', fpath])
        utils.logged_call(['git', '-C', fpath, 'remote', 'add', 'origin', url])

    return lambda *args: ['git', '-C', fpath] + list(args)


def branch_equal_to_merge(git_cmd, state, branch):
    utils.logged_call(git_cmd('fetch', 'origin',
                              'pull/{}/merge'.format(state.num)))
    return utils.silent_call(git_cmd('diff', '--quiet', 'FETCH_HEAD', branch)) == 0


def create_merge(state, repo_cfg, branch, git_cfg, ensure_merge_equal=False):
    base_sha = state.get_repo().ref('heads/' + state.base_ref).object.sha

    state.refresh()

    merge_msg = 'Auto merge of #{} - {}, r={}\n\n{}\n\n{}'.format(
        state.num,
        state.head_ref,
        '<try>' if state.try_ else state.approved_by,
        state.title,
        state.body,
    )

    desc = 'Merge conflict'

    if git_cfg['local_git']:

        git_cmd = init_local_git_cmds(repo_cfg, git_cfg)

        utils.logged_call(git_cmd('fetch', 'origin', state.base_ref,
                                  'pull/{}/head'.format(state.num)))
        utils.silent_call(git_cmd('rebase', '--abort'))
        utils.silent_call(git_cmd('merge', '--abort'))

        if repo_cfg.get('linear', False):
            utils.logged_call(git_cmd('checkout', '-B', branch, state.head_sha))
            try:
                args = [base_sha]
                if repo_cfg.get('autosquash', False):
                    args += ['-i', '--autosquash']
                utils.logged_call(git_cmd('-c', 'user.name=' + git_cfg['name'],
                                          '-c', 'user.email=' + git_cfg['email'],
                                          'rebase', *args))
            except subprocess.CalledProcessError:
                if repo_cfg.get('autosquash', False):
                    utils.silent_call(git_cmd('rebase', '--abort'))
                    if utils.silent_call(git_cmd('rebase', base_sha)) == 0:
                        desc = 'Auto-squashing failed'
            else:
                text = '\nCloses: #{}\nApproved by: {}'.format(state.num, '<try>' if state.try_ else state.approved_by)
                msg_code = 'cat && echo {}'.format(shlex.quote(text))
                env_code = 'export GIT_COMMITTER_NAME={} && export GIT_COMMITTER_EMAIL={} && unset GIT_COMMITTER_DATE'.format(shlex.quote(git_cfg['name']), shlex.quote(git_cfg['email']))
                utils.logged_call(git_cmd('filter-branch', '-f',
                                          '--msg-filter', msg_code,
                                          '--env-filter', env_code,
                                          '{}..'.format(base_sha)))

                if ensure_merge_equal:
                    if not branch_equal_to_merge(git_cmd, state, branch):
                        return ''

                return git_push(git_cmd, branch, state)
        else:
            utils.logged_call(git_cmd('checkout', '-B', 'homu-tmp', state.head_sha))

            ok = True
            if repo_cfg.get('autosquash', False):
                try:
                    merge_base_sha = subprocess.check_output(
                        git_cmd('merge-base', base_sha, state.head_sha)).decode('ascii').strip()
                    utils.logged_call(git_cmd('-c', 'user.name=' + git_cfg['name'],
                                              '-c', 'user.email=' + git_cfg['email'],
                                              'rebase', '-i', '--autosquash',
                                              '--onto', merge_base_sha, base_sha))
                except subprocess.CalledProcessError:
                    desc = 'Auto-squashing failed'
                    ok = False

            if ok:
                utils.logged_call(git_cmd('checkout', '-B', branch, base_sha))
                try:
                    utils.logged_call(git_cmd('-c', 'user.name=' + git_cfg['name'],
                                              '-c', 'user.email=' + git_cfg['email'],
                                              'merge', 'heads/homu-tmp',
                                              '--no-ff', '-m', merge_msg))
                except subprocess.CalledProcessError:
                    pass
                else:
                    if ensure_merge_equal:
                        if not branch_equal_to_merge(git_cmd, state, branch):
                            return ''

                    return git_push(git_cmd, branch, state)
    else:
        if repo_cfg.get('linear', False) or repo_cfg.get('autosquash', False):
            raise RuntimeError('local_git must be turned on to use this feature')

        # if we're merging using the GitHub API, we have no way to predict with
        # certainty what the final result will be so make sure the caller isn't
        # asking us to keep any promises (see also discussions at
        # https://github.com/servo/homu/pull/57)
        assert ensure_merge_equal is False

        if branch != state.base_ref:
            utils.github_set_ref(
                state.get_repo(),
                'heads/' + branch,
                base_sha,
                force=True,
            )

        try:
            merge_commit = state.get_repo().merge(branch, state.head_sha, merge_msg)
        except github3.models.GitHubError as e:
            if e.code != 409:
                raise
        else:
            return merge_commit.sha if merge_commit else ''

    state.set_status('error')
    utils.github_create_status(state.get_repo(), state.head_sha, 'error', '', desc, context='homu')

    state.add_comment(':lock: ' + desc)

    return ''


def pull_is_rebased(state, repo_cfg, git_cfg, base_sha):
    assert git_cfg['local_git']
    git_cmd = init_local_git_cmds(repo_cfg, git_cfg)

    utils.logged_call(git_cmd('fetch', 'origin', state.base_ref,
                              'pull/{}/head'.format(state.num)))

    return utils.silent_call(git_cmd('merge-base', '--is-ancestor',
                                     base_sha, state.head_sha)) == 0


# We could fetch this from GitHub instead, but that API is being deprecated:
# https://developer.github.com/changes/2013-04-25-deprecating-merge-commit-sha/
def get_github_merge_sha(state, repo_cfg, git_cfg):
    assert git_cfg['local_git']
    git_cmd = init_local_git_cmds(repo_cfg, git_cfg)

    if state.mergeable is not True:
        return None

    utils.logged_call(git_cmd('fetch', 'origin',
                              'pull/{}/merge'.format(state.num)))

    return subprocess.check_output(git_cmd('rev-parse', 'FETCH_HEAD')).decode('ascii').strip()


def do_exemption_merge(state, repo_cfg, git_cfg, url, check_merge, reason):

    try:
        merge_sha = create_merge(state, repo_cfg, state.base_ref, git_cfg, check_merge)
    except subprocess.CalledProcessError:
        print('* Unable to create a merge commit for the exempted PR: {}'.format(state))
        traceback.print_exc()
        return False

    if not merge_sha:
        return False

    desc = 'Test exempted'

    state.set_status('success')
    utils.github_create_status(state.get_repo(), state.head_sha, 'success',
                               url, desc, context='homu')
    state.add_comment(':zap: {}: {}.'.format(desc, reason))

    state.merge_sha = merge_sha
    state.save()

    state.fake_merge(repo_cfg)
    return True


def start_build(state, repo_cfgs, buildbot_slots, logger, db, git_cfg):
    if buildbot_slots[0]:
        return True

    assert state.head_sha == state.get_repo().pull_request(state.num).head.sha

    repo_cfg = repo_cfgs[state.repo_label]

    builders = []
    branch = 'try' if state.try_ else 'auto'
    branch = repo_cfg.get('branch', {}).get(branch, branch)

    if 'buildbot' in repo_cfg:
        builders += repo_cfg['buildbot']['try_builders' if state.try_ else 'builders']
    if 'status' in repo_cfg:
        for key, value in repo_cfg['status'].items():
            context = value.get('context')
            if context is not None:
                builders += ['status-' + key]

    if len(builders) is 0:
        raise RuntimeError('Invalid configuration')

    merge_sha = create_merge(state, repo_cfg, branch, git_cfg)
    if not merge_sha:
        return False

    state.init_build_res(builders)
    state.merge_sha = merge_sha

    state.save()

    if 'buildbot' in repo_cfg:
        buildbot_slots[0] = state.merge_sha

    logger.info('Starting build of {}/{}#{} on {}: {}'.format(state.owner,
                                                              state.name,
                                                              state.num, branch, state.merge_sha))

    state.test_started = time.time()
    state.set_status('pending')
    desc = '{} commit {} with merge {}...'.format('Trying' if state.try_ else 'Testing', state.head_sha, state.merge_sha)
    utils.github_create_status(state.get_repo(), state.head_sha, 'pending', '', desc, context='homu')

    state.add_comment(':hourglass: ' + desc)

    return True


def start_rebuild(state, repo_cfgs):
    repo_cfg = repo_cfgs[state.repo_label]

    if 'buildbot' not in repo_cfg or not state.build_res:
        return False

    builders = []
    succ_builders = []

    for builder, info in state.build_res.items():
        if not info['url']:
            return False

        if info['res']:
            succ_builders.append([builder, info['url']])
        else:
            builders.append([builder, info['url']])

    if not builders or not succ_builders:
        return False

    base_sha = state.get_repo().ref('heads/' + state.base_ref).object.sha
    parent_shas = [x['sha'] for x in state.get_repo().commit(state.merge_sha).parents]

    if base_sha not in parent_shas:
        return False
    utils.github_set_ref(state.get_repo(), 'tags/homu-tmp', state.merge_sha, force=True)

    builders.sort()
    succ_builders.sort()

    with buildbot_sess(repo_cfg) as sess:
        for builder, url in builders:
            res = sess.post(url + '/rebuild', allow_redirects=False, data={
                'useSourcestamp': 'exact',
                'comments': 'Initiated by Homu',
            })

            if 'authzfail' in res.text:
                err = 'Authorization failed'
            elif builder in res.text:
                err = ''
            else:
                mat = re.search('<title>(.+?)</title>', res.text)
                err = mat.group(1) if mat else 'Unknown error'

            if err:
                state.add_comment(':bomb: Failed to start rebuilding: `{}`'.format(err))
                return False

    state.test_started = time.time()
    state.set_status('pending')

    msg_1 = 'Previous build results'
    msg_2 = ' for {}'.format(', '.join('[{}]({})'.format(builder, url) for builder, url in succ_builders))
    msg_3 = ' are reusable. Rebuilding'
    msg_4 = ' only {}'.format(', '.join('[{}]({})'.format(builder, url) for builder, url in builders))

    utils.github_create_status(state.get_repo(), state.head_sha, 'pending', '', '{}{}...'.format(msg_1, msg_3), context='homu')

    state.add_comment(':zap: {}{}{}{}...'.format(msg_1, msg_2, msg_3, msg_4))

    return True


def start_build_or_rebuild(state, repo_cfgs, *args):
    if start_rebuild(state, repo_cfgs):
        return True

    return start_build(state, repo_cfgs, *args)


def process_queue(states, repos, repo_cfgs, logger, buildbot_slots, db, git_cfg):
    for repo_label, repo in repos.items():
        repo_states = sorted(states[repo_label].values())

        for state in repo_states:
            if state.priority < repo.treeclosed:
                break
            if state.status == 'pending' and not state.try_:
                break

            elif state.status == 'success' and hasattr(state, 'fake_merge_sha'):
                break

            elif state.status == '' and state.approved_by:
                if start_build_or_rebuild(state, repo_cfgs, buildbot_slots, logger, db, git_cfg):
                    return

            elif state.status == 'success' and state.try_ and state.approved_by:
                state.try_ = False

                state.save()

                if start_build(state, repo_cfgs, buildbot_slots, logger, db, git_cfg):
                    return

        for state in repo_states:
            if state.status == '' and state.try_:
                if start_build(state, repo_cfgs, buildbot_slots, logger, db, git_cfg):
                    return


def fetch_mergeability(mergeable_que):
    re_pull_num = re.compile('(?i)merge (?:of|pull request) #([0-9]+)')

    while True:
        try:
            state, cause = mergeable_que.get()

            if state.status == 'success':
                continue

            pull_request = state.get_repo().pull_request(state.num)
            if pull_request is None or pull_request.mergeable is None:
                time.sleep(5)
                pull_request = state.get_repo().pull_request(state.num)
            mergeable = pull_request is not None and pull_request.mergeable

            if state.mergeable is True and mergeable is False:
                if cause:
                    mat = re_pull_num.search(cause['title'])

                    if mat:
                        issue_or_commit = '#' + mat.group(1)
                    else:
                        issue_or_commit = cause['sha']
                else:
                    issue_or_commit = ''

                state.add_comment(':umbrella: The latest upstream changes{} made this pull request unmergeable. Please resolve the merge conflicts.'.format(
                    ' (presumably {})'.format(issue_or_commit) if issue_or_commit else '',
                ))

            state.set_mergeable(mergeable, que=False)

        except:
            print('* Error while fetching mergeability')
            traceback.print_exc()

        finally:
            mergeable_que.task_done()


def check_timeout(states, queue_handler):
    while True:
        try:
            for repo_label, repo_states in states.items():
                for num, state in repo_states.items():
                    if state.status == 'pending' and time.time() - state.test_started >= TEST_TIMEOUT:
                        print('* Test timed out: {}'.format(state))

                        state.merge_sha = ''
                        state.save()
                        state.set_status('failure')

                        desc = 'Test timed out'
                        utils.github_create_status(state.get_repo(), state.head_sha, 'failure', '', desc, context='homu')
                        state.add_comment(':boom: {}'.format(desc))

                        queue_handler()

        except:
            print('* Error while checking timeout')
            traceback.print_exc()

        finally:
            time.sleep(3600)


def synchronize(repo_label, repo_cfg, logger, gh, states, repos, db, mergeable_que, my_username, repo_labels):
    # FIXME Synchronize updates DB to match state of GitHub. There probably
    # needs to be a user-facing way to sync on demand.
    logger.info('Synchronizing {}...'.format(repo_label))

    repo = gh.repository(repo_cfg['owner'], repo_cfg['name'])

    db_query(db, 'DELETE FROM pull WHERE repo = ?', [repo_label])
    db_query(db, 'DELETE FROM build_res WHERE repo = ?', [repo_label])
    db_query(db, 'DELETE FROM mergeable WHERE repo = ?', [repo_label])

    saved_states = {}
    for num, state in states[repo_label].items():
        saved_states[num] = {
            'merge_sha': state.merge_sha,
            'build_res': state.build_res,
        }

    states[repo_label] = {}
    repos[repo_label] = Repository(repo, repo_label, db)

    for pull in repo.iter_pulls(state='open'):
        db_query(db, 'SELECT status FROM pull WHERE repo = ? AND num = ?', [repo_label, pull.number])
        row = db.fetchone()
        if row:
            status = row[0]
        else:
            status = ''
            for info in utils.github_iter_statuses(repo, pull.head.sha):
                if info.context == 'homu':
                    status = info.state
                    break

        state = PullReqState(pull.number, pull.head.sha, status, db, repo_label, mergeable_que, gh, repo_cfg['owner'], repo_cfg['name'], repos)
        state.title = pull.title
        state.body = pull.body
        state.head_ref = pull.head.repo[0] + ':' + pull.head.ref
        state.base_ref = pull.base.ref
        state.set_mergeable(None)
        state.assignee = pull.assignee.login if pull.assignee else ''

        for comment in pull.iter_comments():
            if comment.original_commit_id == pull.head.sha:
                parse_commands(
                    comment.body,
                    comment.user.login,
                    repo_cfg,
                    state,
                    my_username,
                    db,
                    states,
                    sha=comment.original_commit_id,
                )

        for comment in pull.iter_issue_comments():
            parse_commands(
                comment.body,
                comment.user.login,
                repo_cfg,
                state,
                my_username,
                db,
                states,
            )

        saved_state = saved_states.get(pull.number)
        if saved_state:
            for key, val in saved_state.items():
                setattr(state, key, val)

        state.save()

        states[repo_label][pull.number] = state

    logger.info('Done synchronizing {}!'.format(repo_label))


def main():
    parser = argparse.ArgumentParser()

    dsn = config.get('database')

    parser.add_argument('--dsn', default=dsn,
                        help='Postgresql DSN connection string')
    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s '
                               '%(levelname)s %(message)s',
                        datefmt='%H:%M:%S',
                        level=logging.DEBUG)
    logger = logging.getLogger('autoland')
    stdout_handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(stdout_handler)
    logger.info('starting autoland')

    dbconn = get_dbconn(args.dsn)
    last_error_msg = None
    next_mozreview_update = datetime.datetime.now()
    gh = github3.login(token=config['github']['access_token'])
    user = gh.user()
    cfg_git = config.get('git', {})
    user_email = cfg_git.get('email')
    if user_email is None:
        try:
            user_email = [x for x in gh.iter_emails() if x['primary']][0]['email']
        except IndexError:
            raise RuntimeError('Primary email not set, or "user" scope not granted')
    user_name = cfg_git.get('name', user.name if user.name else user.login)

    states = {}
    repos = {}
    repo_cfgs = {}
    buildbot_slots = ['']
    my_username = user.login
    repo_labels = {}
    mergeable_que = Queue()
    git_cfg = {
        'name': user_name,
        'email': user_email,
        'ssh_key': cfg_git.get('ssh_key', ''),
        'local_git': cfg_git.get('local_git', False),
    }

    for repo_label, repo_cfg in config.get('repo').items():
        repo_cfgs[repo_label] = repo_cfg
        repo_labels[repo_cfg['owner'], repo_cfg['name']] = repo_label

        repo_states = {}
        repos[repo_label] = Repository(None, repo_label, dbconn)

        db_query(dbconn, 'SELECT num, head_sha, status, title, body, head_ref, base_ref, assignee, approved_by, priority, try_, rollup, delegate, merge_sha FROM pull WHERE repo = ?', [repo_label])
        for num, head_sha, status, title, body, head_ref, base_ref, assignee, approved_by, priority, try_, rollup, delegate, merge_sha in dbconn.fetchall():
            state = PullReqState(num, head_sha, status, dbconn, repo_label, mergeable_que, gh, repo_cfg['owner'], repo_cfg['name'], repos)
            state.title = title
            state.body = body
            state.head_ref = head_ref
            state.base_ref = base_ref
            state.assignee = assignee

            state.approved_by = approved_by
            state.priority = int(priority)
            state.try_ = bool(try_)
            state.rollup = bool(rollup)
            state.delegate = delegate
            builders = []
            if merge_sha:
                if 'buildbot' in repo_cfg:
                    builders += repo_cfg['buildbot']['builders']
                if 'status' in repo_cfg:
                    builders += ['status-' + key for key, value in repo_cfg['status'].items() if 'context' in value]
                if len(builders) is 0:
                    raise RuntimeError('Invalid configuration')

                state.init_build_res(builders, use_db=False)
                state.merge_sha = merge_sha

            elif state.status == 'pending':
                # FIXME: There might be a better solution
                state.status = ''

                state.save()

            repo_states[num] = state

        states[repo_label] = repo_states

    os.environ['GIT_SSH'] = os.path.join(os.path.dirname(__file__), 'git_helper.py')
    os.environ['GIT_EDITOR'] = 'cat'

    # Update state of database to match state of GitHub
    synchronize(repo_label, repo_cfg, logger, gh, states, repos, dbconn, mergeable_que, my_username, repo_labels)

    def queue_handler():
        return process_queue(states, repos, repo_cfgs, logger, buildbot_slots, dbconn, git_cfg)

    while True:
        try:
            handle_pending_transplants(logger, dbconn)

            # TODO: In normal configuration, all updates will be posted to the
            # same MozReview instance, so we don't bother tracking failure to
            # post for individual urls. In the future, we might need to
            # support this.
            if datetime.datetime.now() > next_mozreview_update:
                ok = handle_pending_mozreview_updates(logger, dbconn)
                if ok:
                    next_mozreview_update += datetime.timedelta(seconds=1)
                else:
                    next_mozreview_update += MOZREVIEW_RETRY_DELAY
            fetch_mergeability(mergeable_que)
            check_timeout(states, queue_handler)
            process_queue(states, repos, repo_cfgs, logger, buildbot_slots, dbconn, git_cfg)

            time.sleep(0.1)
        except KeyboardInterrupt:
            break
        except psycopg2.InterfaceError:
            dbconn = get_dbconn(args.dsn)
        except:
            # If things go really badly, we might see the same exception
            # thousands of times in a row. There's not really any point in
            # logging it more than once.
            error_msg = traceback.format_exc()
            if error_msg != last_error_msg:
                logger.error(error_msg)
                last_error_msg = error_msg


if __name__ == "__main__":
    main()
