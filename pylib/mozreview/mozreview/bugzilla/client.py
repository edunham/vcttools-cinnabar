# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import posixpath
import xmlrpclib

from urlparse import urlparse, urlunparse

from djblets.siteconfig.models import SiteConfiguration
from djblets.util.decorators import simple_decorator

from mozreview.bugzilla.errors import BugzillaError, BugzillaUrlError
from mozreview.bugzilla.transports import bugzilla_transport


logger = logging.getLogger(__name__)


@simple_decorator
def xmlrpc_to_bugzilla_errors(func):
    def _transform_errors(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except xmlrpclib.Fault as e:
            if e.faultCode == 307:
                # The Bugzilla error message about expired cookies and tokens
                # is a little confusing in the context of MozReview. Override
                # it with one that makes more sense to users.
                fault_string = ('MozReview\'s Bugzilla session has expired. '
                                'Please log out of Review Board and back in, '
                                'and then retry your action.')
            else:
                fault_string = e.faultString

            raise BugzillaError(fault_string, e.faultCode)
        except xmlrpclib.ProtocolError as e:
            raise BugzillaError('ProtocolError: %s' % e.errmsg, e.errcode)
        except IOError as e:
            # Raised when the protocol is invalid or the server can't be
            # found.
            msg = 'unknown'

            if e.args:
                msg = e.args[0]

            raise BugzillaError('IOError: %s' % msg)
    return _transform_errors


class BugzillaAttachmentUpdates(object):
    """Create and update attachments.

    This class provides methods to queue up a series of operations on one
    or more attachments and then execute them all together.  It caches
    attachment information to avoid repeatedly polling Bugzilla.

    All attachments to be created or updated must belong to the same bug.
    """

    def __init__(self, bugzilla, bug_id):
        self.bugzilla = bugzilla
        self.bug_id = bug_id
        self.attachments = []
        self.updates = []
        self.creates = []

    def create_or_update_attachment(self, review_request_id, summary,
                                    comment, url, reviewers):
        """Creates or updates an attachment containing a review-request URL.

        The reviewers argument should be a dictionary mapping reviewer email
        to a boolean indicating if that reviewer has given an r+ on the
        attachment in the past that should be left untouched.
        """

        logger.info('Posting review request %s to bug %d.' %
                    (review_request_id, self.bug_id))
        # Copy because we modify it.
        reviewers = reviewers.copy()
        params = {}
        flags = []
        rb_attachment = None

        self._update_attachments()

        # Find the associated attachment, then go through the review flags.
        for a in self.attachments:

            # Make sure we check for old-style URLs as well.
            if not self.bugzilla._rb_attach_url_matches(a['data'], url):
                continue

            rb_attachment = a

            for f in a.get('flags', []):
                if f['name'] not in ['review', 'feedback']:
                    # We only care about review and feedback flags.
                    continue
                elif f['name'] == 'feedback':
                    # We always clear feedback flags.
                    flags.append({'id': f['id'], 'status': 'X'})
                elif f['status'] == '+' and f['setter'] not in reviewers:
                    # This r+ flag was set manually on bugzilla rather
                    # then through a review on Review Board. Always
                    # clear these flags.
                    flags.append({'id': f['id'], 'status': 'X'})
                elif f['status'] == '+':
                    if not reviewers[f['setter']]:
                        # We should not carry this r+ forward so
                        # re-request review.
                        flags.append({
                            'id': f['id'],
                            'name': 'review',
                            'status': '?',
                            'requestee': f['setter']
                        })

                    reviewers.pop(f['setter'])
                elif 'requestee' not in f or f['requestee'] not in reviewers:
                    # We clear review flags where the requestee is not
                    # a reviewer or someone has manually set r- on the
                    # attachment.
                    flags.append({'id': f['id'], 'status': 'X'})
                elif f['requestee'] in reviewers:
                    # We're already waiting for a review from this user
                    # so don't touch the flag.
                    reviewers.pop(f['requestee'])

            break

        # Add flags for new reviewers.
        # We can't set a missing r+ (if it was manually removed) except in the
        # trivial (and useless) case that the setter and the requestee are the
        # same person.  We could set r? again, but in the event that the
        # reviewer is not accepting review requests, this will block
        # publishing, with no way for the author to fix it.  So we'll just
        # ignore manually removed r+s.
        # This is sorted so behavior is deterministic (this mucks with test
        # output otherwise).
        for reviewer, rplus in sorted(reviewers.iteritems()):
            if not rplus:
                flags.append({
                    'name': 'review',
                    'status': '?',
                    'requestee': reviewer,
                    'new': True
                })

        if rb_attachment:
            params['attachment_id'] = rb_attachment['id']

            if rb_attachment['is_obsolete']:
                params['is_obsolete'] = False
        else:
            params['data'] = url
            params['content_type'] = 'text/x-review-board-request'

        params['file_name'] = 'reviewboard-%d-url.txt' % review_request_id
        params['summary'] = "MozReview Request: %s" % summary
        params['comment'] = comment
        if flags:
            params['flags'] = flags

        if rb_attachment:
            self.updates.append(params)
        else:
            self.creates.append(params)

    def obsolete_review_attachments(self, rb_url):
        """Mark any attachments for a given bug and review request as obsolete.

        This is called when review requests are discarded or deleted. We don't
        want to leave any lingering references in Bugzilla.
        """
        self._update_attachments()

        for a in self.attachments:
            if (self.bugzilla._rb_attach_url_matches(a.get('data'), rb_url) and
                    not a.get('is_obsolete')):
                logger.info('Obsoleting attachment %s on bug %d:' % (
                            a['id'], self.bug_id))
                self.updates.append({
                    'attachment_id': a['id'],
                    'is_obsolete': True
                })

    @xmlrpc_to_bugzilla_errors
    def do_updates(self):
        logger.info('Doing attachment updates for bug %s' % self.bug_id)
        params = self.bugzilla._auth_params({
            'bug_id': self.bug_id,
            'attachments': self.creates + self.updates,
        })

        results = self.bugzilla.proxy.MozReview.attachments(params)

        # The above Bugzilla call is wrapped in a single database transaction
        # and should thus either succeed in creating and updating all
        # attachments or will throw an exception and roll back all changes.
        # However, just to be sure, we check the results.  There's not much
        # we can do in this case, but we'll log an error for investigative
        # purposes.
        # TODO: Display an error in the UI (no easy way to do this without
        # failing the publish).

        ids_to_update = set(u['attachment_id'] for u in self.updates)
        ids_to_update.difference_update(results['attachments_modified'].keys())

        if ids_to_update:
            logger.error('Failed to update the following attachments: %s' %
                         ids_to_update)

        num_to_create = len(self.creates)
        num_created = len(results['attachments_created'])

        if num_to_create != num_created:
            logger.error('Tried to create %s attachments but %s reported as '
                         'created.' % (num_to_create, num_created))

        self.creates = []
        self.updates = []

    def _update_attachments(self):
        if not self.attachments:
            self.attachments = self.bugzilla.get_rb_attachments(self.bug_id)


class Bugzilla(object):
    """
    Interface to a Bugzilla system.

    At the moment this uses the XMLRPC API.  It should probably be converted
    to REST at some point.

    General FIXME: try to get more specific errors out of xmlrpclib.Fault
    exceptions.
    """

    user_fields = ['id', 'email', 'real_name', 'can_login']

    def __init__(self, api_key=None, xmlrpc_url=None):
        self.api_key = api_key
        self._transport = None
        self._proxy = None

        siteconfig = SiteConfiguration.objects.get_current()

        if xmlrpc_url:
            self.xmlrpc_url = xmlrpc_url
        else:
            self.xmlrpc_url = siteconfig.get('auth_bz_xmlrpc_url')

        if not self.xmlrpc_url:
            raise BugzillaUrlError('no XMLRPC URL')

        # We only store the xmlrpc URL currently. We should eventually store
        # the Bugzilla base URL and derive the XMLRPC URL from it.
        u = urlparse(self.xmlrpc_url)
        root = posixpath.dirname(u.path).rstrip('/') + '/'
        self.base_url = urlunparse((u.scheme, u.netloc, root, '', '', ''))

    @xmlrpc_to_bugzilla_errors
    def log_in(self, username, password, cookie=False):
        if cookie:
            # Username and password are actually bugzilla cookies.
            self.transport.set_bugzilla_cookies(username, password)
            user_id = username
        else:
            self.transport.remove_bugzilla_cookies()

            try:
                result = self.proxy.User.login({'login': username,
                                                'password': password})
            except xmlrpclib.Fault as e:
                if e.faultCode == 300:
                    logger.error('Login failure for user %s: '
                                 'invalid username or password.' % username)
                    return None
                elif e.faultCode == 301:
                    logger.error('Login failure for user %s: '
                                 'user is disabled.' % username)
                    return None
                raise

            user_id = result['id']

        params = {
            'ids': [user_id],
            'include_fields': self.user_fields,
        }

        try:
            return self.proxy.User.get(params)
        except xmlrpclib.Fault as e:
            raise

    @xmlrpc_to_bugzilla_errors
    def get_user(self, username):
        params = self._auth_params({
            'names': [username],
            'include_fields': self.user_fields
        })
        return self.proxy.User.get(params)

    @xmlrpc_to_bugzilla_errors
    def query_users(self, query):
        params = self._auth_params({
            'match': [query],
            'include_fields': self.user_fields
        })
        return self.proxy.User.get(params)

    @xmlrpc_to_bugzilla_errors
    def get_user_from_userid(self, userid):
        """Convert an integer user ID to string username."""
        params = self._auth_params({
            'ids': [userid],
            'include_fields': self.user_fields
        })
        return self.proxy.User.get(params)

    @xmlrpc_to_bugzilla_errors
    def post_comment(self, bug_id, comment):
        params = self._auth_params({
            'id': bug_id,
            'comment': comment
        })
        logger.info('Posting comment on bug %d.' % bug_id)
        return self.proxy.Bug.add_comment(params)

    @xmlrpc_to_bugzilla_errors
    def is_bug_confidential(self, bug_id):
        # We don't need to authenticate here; if we can't get the bug,
        # that itself means it's confidential.
        params = {'ids': [bug_id], 'include_fields': ['groups']}

        logger.info('Checking if bug %d is confidential.' % bug_id)
        try:
            groups = self.proxy.Bug.get(params)['bugs'][0]['groups']
        except xmlrpclib.Fault as e:
            if e.faultCode == 102:
                logger.info('Bug %d is confidential.' % bug_id)
                return True
            raise

        logger.info('Bug %d confidential: %s.' % (bug_id, bool(groups)))
        return bool(groups)

    @xmlrpc_to_bugzilla_errors
    def get_rb_attachments(self, bug_id):
        """Get all attachments that contain review-request URLs."""

        params = self._auth_params({
            'ids': [bug_id],
            'include_fields': ['id', 'data', 'content_type', 'is_obsolete',
                               'flags']
        })

        return [a for a
                in self.proxy.Bug.attachments(params)['bugs'][str(bug_id)]
                if a['content_type'] == 'text/x-review-board-request']

    def _rb_attach_url_matches(self, attach_url, rb_url):
        # Make sure we check for old-style URLs as well.
        return (attach_url == rb_url or
                '%sdiff/#index_header' % attach_url == rb_url)

    def _get_review_request_attachment(self, bug_id, rb_url):
        """Obtain a Bugzilla attachment for a review request."""
        for a in self.get_rb_attachments(bug_id):
            if self._rb_attach_url_matches(a.get('data'), rb_url):
                return a

        return None

    @xmlrpc_to_bugzilla_errors
    def set_review_flag(self, bug_id, flag, reviewer, rb_url, comment=None):
        """Create, update or clear a review flag.

        Does nothing if the reviewer has already set the given review flag
        on the attachment.

        Returns a boolean indicating whether we added/cleared a review flag
        on the attachment or not.
        """

        logger.info('%s from %s on bug %d.' % (flag or 'Clearing flag',
                                               reviewer, bug_id))

        flag = flag.strip()

        allowed_flags = {
            'r+': '+',
            'r?': '?',
            'r-': '-',
            '': 'X'
        }
        if flag not in allowed_flags:
            logger.error('Flag change not allowed, flag must be one of '
                         '%s.' % str(allowed_flags))
            return False

        # Convert the flag to its bugzilla-friendly version.
        flag = allowed_flags[flag]

        rb_attachment = self._get_review_request_attachment(bug_id, rb_url)
        if not rb_attachment:
            logger.error('Could not find attachment for Review Board URL %s '
                         'in bug %s.' % (rb_url, bug_id))
            return False

        flag_list = rb_attachment.get('flags', [])
        flag_obj = {'name': 'review', 'status': flag}

        # Only keep review flags.
        review_flags = [f for f in flag_list if f['name'] == 'review']

        for f in review_flags:
            # Bugzilla attachments have a requestee only if the status is `?`.
            # In the other cases requestee == setter.
            if ((reviewer == f.get('requestee') and f['status'] == '?')  or
                    (reviewer == f.get('setter') and f['status'] != '?')):

                # Always clear the flag if desired status is not r+.
                if flag == '?':
                    flag_obj['status'] = 'X'

                # Flag is already set, don't change it.
                elif f['status'] == flag:
                    logger.info('r%s already set.' % flag)
                    return False

                flag_obj['id'] = f['id']
                break
        else:
            # The reviewer did not have a flag on the attachment.
            if flag == 'X':
                # This shouldn't happen under normal circumstances, but if it
                # does log it.
                logger.info('No flag to clear for %s on %s' % (
                    reviewer, rb_attachment
                ))
                return False
            elif flag not in ('+', '-'):
                # If the reviewer has no +/- set and they submit a comment
                # don't create any flag.
                return False

            # Flag not found, let's create a new one.
            flag_obj['new'] = True

        logging.info('sending flag: %s' % flag_obj)

        params = self._auth_params({
            'ids': [rb_attachment['id']],
            'flags': [flag_obj],
        })

        if comment:
            params['comment'] = comment

        self.proxy.Bug.update_attachment(params)
        return True

    @xmlrpc_to_bugzilla_errors
    def r_plus_attachment(self, bug_id, reviewer, rb_url, comment=None):
        """Set a review flag to "+".

        Does nothing if the reviewer has already r+ed the attachment.
        Updates flag if a corresponding r? is found; otherwise creates a
        new flag.

        We return a boolean indicating whether we r+ed the attachment.
        """

        return self.set_review_flag(bug_id, 'r+', reviewer, rb_url, comment)

    @xmlrpc_to_bugzilla_errors
    def cancel_review_request(self, bug_id, reviewer, rb_url, comment=None):
        """Cancel an r? or r+ flag on a Bugzilla attachment.

        We return a boolean indicating whether we cancelled a review request.
        This is so callers can do something with the comment (which won't get
        posted unless the review flag was cleared).
        """
        logger.info('maybe cancelling r? from %s on bug %d.' % (reviewer,
                                                                bug_id))
        return self.set_review_flag(bug_id, '', reviewer, rb_url, comment)

    @xmlrpc_to_bugzilla_errors
    def valid_api_key(self, username, api_key):
        try:
            return self.proxy.User.valid_login({
                'login': username,
                'api_key': api_key,
            })
        except xmlrpclib.Fault as e:
            # Invalid API-key formats (e.g. not 40 characters long) or expired
            # API keys will raise an error, but for our purposes we just
            # consider them as invalid proper API keys, particularly so we can
            # try another type of authentication.
            if e.faultCode == 306:
                return False

            raise

    def _auth_params(self, params):
        if not self.api_key:
            raise BugzillaError('There is no Bugzilla API key on record for '
                                'this user. Please log into MozReview\'s '
                                'UI to have one generated.')

        params['api_key'] = self.api_key
        return params

    @property
    def transport(self):
        if self._transport is None:
            self._transport = bugzilla_transport(self.xmlrpc_url)

        return self._transport

    @property
    def proxy(self):
        if self._proxy is None:
            self._proxy = xmlrpclib.ServerProxy(self.xmlrpc_url,
                                                self.transport)

        return self._proxy
