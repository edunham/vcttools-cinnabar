# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Provide enhancements to hg.mozilla.org

Config Options
==============

hgmo.mozbuildinfoenabled
   Whether evaluation of moz.build files for metadata is enabled from hgweb
   requests. This is disabled by default.

hgmo.mozbuildinfowrapper
   A command to execute to obtain moz.build file info.

   The value MUST not contain any quote characters. The value is split on
   whitespace and a new process is spawned with the result. Therefore, the
   first argument must be the absolute path to an executable.

   The literal "%repo%" will be replaced with the path of the repository
   being operated on.

   See "moz.build wrapper commands" for more.

hgmo.replacebookmarks
   When set, `hg pull` and other bookmark application operations will replace
   local bookmarks with incoming bookmarks instead of doing the more
   complicated default behavior, which includes creating diverged bookmarks.

hgmo.convertsource
   When set and a changeset has a ``convert_revision`` extra data attribute,
   the changeset template data will contain a link to the source revision it
   was converted from.

   Value is a relative or absolute path to the repo. e.g.
   ``/mozilla-central``.

moz.build Wrapper Commands
==========================

Some moz.build file info lookups are performed via a separate process. The
process to invoke is defined by the ``hgmo.mozbuildinfowrapper`` option.

The reason for this is security. moz.build files are Python files that must
be executed in a Python interpreter. Python is notoriously difficult (read:
impossible) to sandbox properly. Therefore, we cannot trust that moz.build
files don't contain malicious code which may escape the moz.build execution
context and compromise the Mercurial server. Wrapper commands exist to
allow a more secure execution environment/sandbox to be established so escapes
from the Python moz.build environment can't gain additional permissions.

The program defined by ``hgmo.mozbuildinfowrapper`` is invoked and parameters
are passed to it as JSON via stdin. The stdin pipe is closed once all JSON
has been sent. The JSON properties are:

repo
   Path to repository being operated on.

node
   40 character hex SHA-1 of changeset being queried.

paths
   Array of paths we're interested in information about.

Successful executions will emit JSON on stdout and exit with code 0.
Non-0 exit codes will result in output being logged locally and a generic
message being returned to the user. stderr is logged in all cases.

Wrapper commands typically read arguments, set up a secure execution
environment, then invoke the relevant mozbuild APIs to obtain requested info.

Wrapper commands may invoke ``hg mozbuildinfo --pipemode`` to retrieve
moz.build info. In fact, the wrapper command itself can be defined as this
string. Of course, no security will be provided.
"""

import json
import os
import subprocess
import types

from mercurial.i18n import _
from mercurial.node import bin, short
from mercurial import (
    bookmarks,
    cmdutil,
    commands,
    encoding,
    error,
    extensions,
    hg,
    revset,
    templatefilters,
    util,
    wireproto,
)
from mercurial.hgweb import (
    webcommands,
    webutil,
)
from mercurial.hgweb.common import (
    HTTP_OK,
)
from mercurial.hgweb.protocol import (
    webproto,
)


OUR_DIR = os.path.dirname(__file__)
ROOT = os.path.normpath(os.path.join(OUR_DIR, '..', '..'))
execfile(os.path.join(OUR_DIR, '..', 'bootstrap.py'))

import mozautomation.commitparser as commitparser
import mozhg.mozbuildinfo as mozbuildinfo


minimumhgversion = '4.0'
testedwith = '4.0'

cmdtable = {}
command = cmdutil.command(cmdtable)


@templatefilters.templatefilter('buglink')
def buglink(text):
    """Any text. Hyperlink to Bugzilla."""
    return commitparser.add_hyperlinks(text)


def addmetadata(repo, ctx, d, onlycheap=False):
    """Add changeset metadata for hgweb templates."""
    description = encoding.fromlocal(ctx.description())

    d['bugs'] = []
    for bug in commitparser.parse_bugs(description):
        d['bugs'].append({
            'no': str(bug),
            'url': 'https://bugzilla.mozilla.org/show_bug.cgi?id=%s' % bug,
        })

    d['reviewers'] = []
    for reviewer in commitparser.parse_reviewers(description):
        d['reviewers'].append({
            'name': reviewer,
            'revset': 'reviewer(%s)' % reviewer,
        })

    d['backsoutnodes'] = []
    backouts = commitparser.parse_backouts(description)
    if backouts:
        for node in backouts[0]:
            try:
                bctx = repo[node]
                d['backsoutnodes'].append({'node': bctx.hex()})
            except error.RepoLookupError:
                pass

    # Repositories can define which TreeHerder repository they are associated
    # with.
    treeherder = repo.ui.config('mozilla', 'treeherder_repo')
    if treeherder:
        d['treeherderrepourl'] = 'https://treeherder.mozilla.org/#/jobs?repo=%s' % treeherder
        d['treeherderrepo'] = treeherder

        push = repo.pushlog.pushfromchangeset(ctx)
        # Don't print Perfherder link on non-publishing repos (like Try)
        # because the previous push likely has nothing to do with this
        # push.
        if push and push.nodes and repo.ui.configbool('phases', 'publish', True):
            lastpushhead = repo[push.nodes[0]].hex()
            d['perfherderurl'] = (
                'https://treeherder.mozilla.org/perf.html#/compare?'
                'originalProject=%s&'
                'originalRevision=%s&'
                'newProject=%s&'
                'newRevision=%s') % (treeherder, push.nodes[-1],
                                     treeherder, lastpushhead)

    # If this changeset was converted from another one and we know which repo
    # it came from, add that metadata.
    convertrevision = ctx.extra().get('convert_revision')
    if convertrevision:
        sourcerepo = repo.ui.config('hgmo', 'convertsource')
        if sourcerepo:
            d['convertsourcepath'] = sourcerepo
            d['convertsourcenode'] = convertrevision

    if onlycheap:
        return

    # Obtain the Gecko/app version/milestone.
    #
    # We could probably only do this if the repo is a known app repo (by
    # looking at the initial changeset). But, path based lookup is relatively
    # fast, so just do it. However, we need this in the "onlycheap"
    # section because resolving manifests is relatively slow and resolving
    # several on changelist pages may add seconds to page load times.
    try:
        fctx = repo.filectx('config/milestone.txt', changeid=ctx.node())
        lines = fctx.data().splitlines()
        lines = [l for l in lines if not l.startswith('#') and l.strip()]

        if lines:
            d['milestone'] = lines[0].strip()
    except error.LookupError:
        pass

    # Look for changesets that back out this one.
    #
    # We limit the distance we search for backouts because an exhaustive
    # search could be very intensive. e.g. you load up the root commit
    # on a repository with 200,000 changesets and that commit is never
    # backed out. This finds most backouts because backouts typically happen
    # shortly after a bad commit is introduced.
    thisshort = short(ctx.node())
    count = 0
    searchlimit = repo.ui.configint('hgmo', 'backoutsearchlimit', 100)
    for bctx in repo.set('%ld::', [ctx.rev()]):
        count += 1
        if count >= searchlimit:
            break

        backouts = commitparser.parse_backouts(
            encoding.fromlocal(bctx.description()))
        if backouts and thisshort in backouts[0]:
            d['backedoutbynode'] = bctx.hex()
            break


def changesetentry(orig, web, req, tmpl, ctx):
    """Wraps webutil.changesetentry to provide extra metadata."""
    d = orig(web, req, tmpl, ctx)
    addmetadata(web.repo, ctx, d)
    return d


def changelistentry(orig, web, ctx, tmpl):
    d = orig(web, ctx, tmpl)
    addmetadata(web.repo, ctx, d, onlycheap=True)
    return d


def mozbuildinfowebcommand(web, req, tmpl):
    """Web command handler for the "mozbuildinfo" command."""
    repo = web.repo

    # TODO we should be using the templater instead of emitting JSON directly.
    # But this requires not having the JSON formatter from the
    # pushlog-legacy.py extension.

    if not repo.ui.configbool('hgmo', 'mozbuildinfoenabled', False):
        req.respond(HTTP_OK, 'application/json')
        return json.dumps({'error': 'moz.build evaluation is not enabled for this repo'})

    if not repo.ui.config('hgmo', 'mozbuildinfowrapper'):
        req.respond(HTTP_OK, 'application/json')
        return json.dumps({'error': 'moz.build wrapper command not defined; refusing to execute'})

    rev = 'tip'
    if 'node' in req.form:
        rev = req.form['node'][0]

    ctx = repo[rev]
    paths = req.form.get('p', ctx.files())

    pipedata = json.dumps({
        'repo': repo.root,
        'node': ctx.hex(),
        'paths': paths,
    })

    args = repo.ui.config('hgmo', 'mozbuildinfowrapper')
    # Should be verified by extsetup. Double check since any process invocation
    # has security implications.
    assert '"' not in args
    assert "'" not in args
    args = args.split()
    args = [a.replace('%repo%', repo.root) for a in args]

    # We do not use a shell because it is only a vector for pain and possibly
    # security issues.
    # We close file descriptors out of security paranoia.
    # We switch cwd of the process so state from the current directory isn't
    # picked up.
    try:
        p = subprocess.Popen(args,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             cwd='/',
                             shell=False,
                             close_fds=True)
    except OSError as e:
        repo.ui.log('error invoking moz.build info process: %s\n' % e.errno)
        return json.dumps({'error': 'unable to invoke moz.build info process'})

    stdout, stderr = p.communicate(pipedata)

    req.respond(HTTP_OK, 'application/json')

    if p.returncode:
        repo.ui.log('failure obtaining moz.build info: stdout: %s; '
                    'stderr: %s\n' % (stdout, stderr))
        return json.dumps({'error': 'unable to obtain moz.build info'},
                          indent=2)
    elif stderr.strip():
        repo.ui.log('moz.build evaluation output: %s\n' % stderr.strip())

    # Round trip to ensure we have valid JSON.
    try:
        d = json.loads(stdout)
        return json.dumps(d, indent=2, sort_keys=True)
    except Exception:
        return json.dumps({'error': 'invalid JSON returned; report this error'},
                          indent=2)


def infowebcommand(web, req, tmpl):
    """Get information about the specified changeset(s).

    This is a legacy API from before the days of Mercurial's built-in JSON
    API. It is used by unidentified parts of automation. Over time these
    consumers should transition to the modern/native JSON API.
    """
    if 'node' not in req.form:
        return tmpl('error', error={'error': "missing parameter 'node'"})

    csets = []
    for node in req.form['node']:
        ctx = web.repo[node]
        csets.append({
            'rev': ctx.rev(),
            'node': ctx.hex(),
            'user': ctx.user(),
            'date': ctx.date(),
            'description': ctx.description(),
            'branch': ctx.branch(),
            'tags': ctx.tags(),
            'parents': [p.hex() for p in ctx.parents()],
            'children': [c.hex() for c in ctx.children()],
            'files': ctx.files(),
        })

    return tmpl('info', csets=csets)


def headdivergencewebcommand(web, req, tmpl):
    """Get information about divergence between this repo and a changeset.

    This API was invented to be used by MozReview to obtain information about
    how a repository/head has progressed/diverged since a commit was submitted
    for review.

    It is assumed that this is running on the canonical/mainline repository.
    Changes in other repositories must be rebased onto or merged into
    this repository.
    """
    if 'node' not in req.form:
        return tmpl('error', error={'error': "missing parameter 'node'"})

    repo = web.repo

    paths = set(req.form.get('p', []))
    basectx = repo[req.form['node'][0]]

    # Find how much this repo has changed since the requested changeset.
    # Our heuristic is to find the descendant head with the highest revision
    # number. Most (all?) repositories we care about for this API should have
    # a single head per branch. And we assume the newest descendant head is
    # the one we care about the most. We don't care about branches because
    # if a descendant is on different branch, then the repo has likely
    # transitioned to said branch.
    #
    # If we ever consolidate Firefox repositories, we'll need to reconsider
    # this logic, especially if release repos with their extra branches/heads
    # are involved.

    # Specifying "start" only gives heads that are descendants of "start."
    headnodes = repo.changelog.heads(start=basectx.node())

    headrev = max(repo[n].rev() for n in headnodes)
    headnode = repo[headrev].node()

    betweennodes, outroots, outheads = \
        repo.changelog.nodesbetween([basectx.node()], [headnode])

    # nodesbetween returns base node. So prune.
    betweennodes = betweennodes[1:]

    commitsbehind = len(betweennodes)

    # If rev 0 or a really old revision is passed in, we could DoS the server
    # by having to iterate nearly all changesets. Establish a cap for number
    # of changesets to examine.
    maxnodes = repo.ui.configint('hgmo', 'headdivergencemaxnodes', 1000)
    filemergesignored = False
    if len(betweennodes) > maxnodes:
        betweennodes = []
        filemergesignored = True

    filemerges = {}
    for node in betweennodes:
        ctx = repo[node]

        files = set(ctx.files())
        for p in files & paths:
            filemerges.setdefault(p, []).append(ctx.hex())

    return tmpl('headdivergence', commitsbehind=commitsbehind,
                filemerges=filemerges, filemergesignored=filemergesignored)


def automationrelevancewebcommand(web, req, tmpl):
    if 'node' not in req.form:
        return tmpl('error', error={'error': "missing parameter 'node'"})

    repo = web.repo
    deletefields = set([
        'bookmarks',
        'branch',
        'branches',
        'changelogtag',
        'child',
        'inbranch',
        'parent',
        'phase',
        'tags',
    ])

    csets = []
    # Query an unfiltered repo because sometimes automation wants to run against
    # changesets that have since become hidden. The response exposes whether the
    # requested node is visible, so consumers can make intelligent decisions
    # about what to do if the changeset isn't visible.
    for ctx in repo.unfiltered().set('automationrelevant(%r)', req.form['node'][0]):
        entry = webutil.changelistentry(web, ctx, tmpl)
        # Some items in changelistentry are generators, which json.dumps()
        # can't handle. So we expand them.
        for k, v in entry.items():
            # "files" is a generator that attempts to call a template.
            # Don't even bother and just repopulate it.
            if k == 'files':
                entry['files'] = sorted(ctx.files())
            elif k == 'allparents':
                entry['parents'] = [p['node'] for p in v()]
                del entry['allparents']
            # These aren't interesting to us, so prune them. The
            # original impetus for this was because "changelogtag"
            # isn't part of the json template and adding it is non-trivial.
            elif k in deletefields:
                del entry[k]
            elif isinstance(v, types.GeneratorType):
                entry[k] = list(v)

        csets.append(entry)

    # Advertise whether the requested revision is visible (non-obsolete).
    if csets:
        visible = csets[-1]['node'] in repo
    else:
        visible = None

    data = {
        'changesets': csets,
        'visible': visible,
    }

    req.respond(HTTP_OK, 'application/json')
    # We use latin1 as the encoding here because all data should be treated as
    # byte strings. ensure_ascii will escape non-ascii values using \uxxxx.
    return json.dumps(data, indent=2, sort_keys=True, encoding='latin1',
                      separators=(',', ': '))


def revset_reviewer(repo, subset, x):
    """``reviewer(REVIEWER)``

    Changesets reviewed by a specific person.
    """
    l = revset.getargs(x, 1, 1, 'reviewer requires one argument')
    n = encoding.lower(revset.getstring(l[0], 'reviewer requires a string'))

    # Do not use a matcher here because regular expressions are not safe
    # for remote execution and may DoS the server.
    def hasreviewer(r):
        for reviewer in commitparser.parse_reviewers(repo[r].description()):
            if encoding.lower(reviewer) == n:
                return True

        return False

    return subset.filter(hasreviewer)


def revset_automationrelevant(repo, subset, x):
    """``automationrelevant(set)``

    Changesets relevant to scheduling in automation.

    Given a revset that evaluates to a single revision, will return that
    revision and any ancestors that are part of the same push unioned with
    non-public ancestors.
    """
    s = revset.getset(repo, revset.fullreposet(repo), x)
    if len(s) > 1:
        raise util.Abort('can only evaluate single changeset')

    ctx = repo[s.first()]
    revs = set([ctx.rev()])

    # The pushlog is used to get revisions part of the same push as
    # the requested revision.
    pushlog = getattr(repo, 'pushlog', None)
    if pushlog:
        push = repo.pushlog.pushfromchangeset(ctx)
        for n in push.nodes:
            pctx = repo[n]
            if pctx.rev() <= ctx.rev():
                revs.add(pctx.rev())

    # Union with non-public ancestors if configured. By default, we only
    # consider changesets from the push. However, on special repositories
    # (namely Try), we want changesets from previous pushes to come into
    # play too.
    if repo.ui.configbool('hgmo', 'automationrelevantdraftancestors', False):
        for rev in repo.revs('::%d & not public()', ctx.rev()):
            revs.add(rev)

    return subset & revset.baseset(revs)


def bmupdatefromremote(orig, ui, repo, remotemarks, path, trfunc, explicit=()):
    """Custom bookmarks applicator that overwrites with remote state.

    The default bookmarks merging code adds divergence. When replicating from
    master to mirror, a bookmark force push could result in divergence on the
    mirror during `hg pull` operations. We install our own code that replaces
    the complicated merging algorithm with a simple "remote wins" version.
    """
    if not ui.configbool('hgmo', 'replacebookmarks', False):
        return orig(ui, repo, remotemarks, path, trfunc, explicit=explicit)

    localmarks = repo._bookmarks

    if localmarks == remotemarks:
        return

    ui.status('remote bookmarks changed; overwriting\n')
    localmarks.clear()
    for bm, node in remotemarks.items():
        localmarks[bm] = bin(node)
    tr = trfunc()
    localmarks.recordchange(tr)


def servehgmo(orig, ui, repo, *args, **kwargs):
    """Wraps commands.serve to provide --hgmo flag."""
    if kwargs.get('hgmo', False):
        kwargs['style'] = 'gitweb_mozilla'
        kwargs['templates'] = os.path.join(ROOT, 'hgtemplates')

        # ui.copy() is funky. Unless we do this, extension settings get
        # lost when calling hg.repository().
        ui = ui.copy()

        def setconfig(name, paths):
            ui.setconfig('extensions', name,
                         os.path.join(ROOT, 'hgext', *paths))

        setconfig('pushlog', ['pushlog'])
        setconfig('pushlog-feed', ['pushlog-legacy', 'pushlog-feed.py'])

        # Since new extensions may have been flagged for loading, we need
        # to obtain a new repo instance to a) trigger loading of these
        # extensions b) force extensions' reposetup function to run.
        repo = hg.repository(ui, repo.root)

    return orig(ui, repo, *args, **kwargs)


@command('mozbuildinfo', [
    ('r', 'rev', '.', _('revision to query'), _('REV')),
    ('', 'pipemode', False, _('accept arguments from stdin')),
    ], _('show files info from moz.build files'),
    optionalrepo=True)
def mozbuildinfocommand(ui, repo, *paths, **opts):
    if opts['pipemode']:
        data = json.loads(ui.fin.read())

        repo = hg.repository(ui, path=data['repo'])
        ctx = repo[data['node']]
        paths = data['paths']
    else:
        ctx = repo[opts['rev']]

    try:
        d = mozbuildinfo.filesinfo(repo, ctx, paths=paths)
    except Exception as e:
        d = {'error': 'Exception reading moz.build info: %s' % str(e)}

    if not d:
        d = {'error': 'no moz.build info available'}

    # TODO send data to templater.
    # Use stable output and indentation to make testing easier.
    ui.write(json.dumps(d, indent=2, sort_keys=True))
    ui.write('\n')
    return


def clonebundleswireproto(orig, repo, proto):
    """Wraps wireproto.clonebundles."""
    return processbundlesmanifest(repo, proto, orig(repo, proto))


def bundleclonewireproto(orig, repo, proto):
    """Wraps wireproto.bundles."""
    return processbundlesmanifest(repo, proto, orig(repo, proto))


def processbundlesmanifest(repo, proto, manifest):
    """Processes a bundleclone/clonebundles manifest.

    We examine source IP addresses and advertise URLs for the same
    AWS region if the source is in AWS.
    """
    # Delay import because this extension can be run on local
    # developer machines.
    import ipaddress

    if not isinstance(proto, webproto):
        return manifest

    awspath = repo.ui.config('hgmo', 'awsippath')
    if not awspath:
        return manifest

    # Mozilla's load balancers add a X-Cluster-Client-IP header to identify the
    # actual source IP, so prefer it.
    sourceip = proto.req.env.get('HTTP_X_CLUSTER_CLIENT_IP',
                                 proto.req.env.get('REMOTE_ADDR'))
    if not sourceip:
        return manifest

    origlines = [l for l in manifest.splitlines()]
    # ec2 region not listed in any manifest entries. This is weird but it means
    # there is nothing for us to do.
    if not any('ec2region=' in l for l in origlines):
        return manifest

    try:
        # constructor insists on unicode instances.
        sourceip = ipaddress.IPv4Address(sourceip.decode('ascii'))

        with open(awspath, 'rb') as fh:
            awsdata = json.load(fh)

        for ipentry in awsdata['prefixes']:
            network = ipaddress.IPv4Network(ipentry['ip_prefix'])

            if sourceip not in network:
                continue

            region = ipentry['region']

            filtered = [l for l in origlines if 'ec2region=%s' % region in l]
            # No manifest entries for this region. Ignore match and try others.
            if not filtered:
                continue

            # We prioritize stream clone bundles to AWS clients because they are
            # the fastest way to clone and we want our automation to be fast.
            def mancmp(a, b):
                packed = 'BUNDLESPEC=none-packed1'
                stream = 'stream='

                if packed in a and packed not in b:
                    return -1
                if packed in b and packed not in a:
                    return 1

                if stream in a and stream not in b:
                    return -1
                if stream in b and stream not in a:
                    return 1

                return 0

            filtered = sorted(filtered, cmp=mancmp)

            # We got a match. Write out the filtered manifest (with a trailing newline).
            filtered.append('')
            return '\n'.join(filtered)

        return manifest

    except Exception as e:
        repo.ui.log('hgmo', 'exception filtering bundle source IPs: %s\n', e)
        return manifest


def filelog(orig, web, req, tmpl):
    """Wraps webcommands.filelog to provide pushlog metadata to template."""

    if hasattr(web.repo, 'pushlog'):

        class _tmpl(object):

            def __init__(self):
                self.defaults = tmpl.defaults

            def __call__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                return self

        class _ctx(object):

            def __init__(self, hex):
                self._hex = hex

            def hex(self):
                return self._hex

        t = orig(web, req, _tmpl())
        for entry in t.kwargs['entries']:
            push = web.repo.pushlog.pushfromchangeset(_ctx(entry['node']))
            if push:
                entry['pushid'] = push.pushid
                entry['pushdate'] = util.makedate(push.when)
            else:
                entry['pushid'] = None
                entry['pushdate'] = None

        return tmpl(*t.args, **t.kwargs)
    else:
        return orig(web, req, tmpl)


def extsetup(ui):
    extensions.wrapfunction(webutil, 'changesetentry', changesetentry)
    extensions.wrapfunction(webutil, 'changelistentry', changelistentry)
    extensions.wrapfunction(bookmarks, 'updatefromremote', bmupdatefromremote)
    extensions.wrapfunction(webcommands, 'filelog', filelog)

    revset.symbols['reviewer'] = revset_reviewer
    revset.safesymbols.add('reviewer')

    revset.symbols['automationrelevant'] = revset_automationrelevant
    revset.safesymbols.add('automationrelevant')

    # Install IP filtering for bundle URLs.

    # Build-in command from core Mercurial.
    extensions.wrapcommand(wireproto.commands, 'clonebundles', clonebundleswireproto)

    # Legacy bundleclone command. Need to support until all clients run
    # 3.6+.
    if 'bundles' in wireproto.commands:
        extensions.wrapcommand(wireproto.commands, 'bundles', bundleclonewireproto)

    entry = extensions.wrapcommand(commands.table, 'serve', servehgmo)
    entry[1].append(('', 'hgmo', False,
                     'Run a server configured like hg.mozilla.org'))

    wrapper = ui.config('hgmo', 'mozbuildinfowrapper')
    if wrapper:
        if '"' in wrapper or "'" in wrapper:
            raise util.Abort('quotes may not appear in hgmo.mozbuildinfowrapper')

    setattr(webcommands, 'mozbuildinfo', mozbuildinfowebcommand)
    webcommands.__all__.append('mozbuildinfo')

    setattr(webcommands, 'info', infowebcommand)
    webcommands.__all__.append('info')

    setattr(webcommands, 'headdivergence', headdivergencewebcommand)
    webcommands.__all__.append('headdivergence')

    setattr(webcommands, 'automationrelevance', automationrelevancewebcommand)
    webcommands.__all__.append('automationrelevance')
