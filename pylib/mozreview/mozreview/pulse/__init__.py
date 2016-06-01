from __future__ import unicode_literals

import json
import time

from reviewboard.admin.server import get_server_url
from reviewboard.extensions.hooks import SignalHook
from reviewboard.reviews.models import ReviewRequest
from reviewboard.reviews.signals import (
    reply_published,
    review_published,
    review_request_published,
)

from mozillapulse import publishers
from mozillapulse.messages import base

from mozreview.decorators import if_ext_enabled
from mozreview.extra_data import (
    COMMITS_KEY,
    fetch_commit_data,
    is_parent,
    is_pushed,
)


def initialize_pulse_handlers(extension):
    SignalHook(extension, reply_published, handle_reply_published)
    SignalHook(extension, review_published, handle_review_published)
    SignalHook(extension, review_request_published,
               handle_commits_published)


@if_ext_enabled
def handle_reply_published(extension=None, **kwargs):
    """Handle sending a message when a review reply is published."""
    reply = kwargs.get('reply')
    if not reply:
        return

    # A review reply is a special kind of a review. So use the same code path.
    _send_message_from_review(extension, reply)


@if_ext_enabled
def handle_review_published(extension=None, **kwargs):
    """Handle sending a message when a review is published."""
    review = kwargs.get('review')
    if not review:
        return

    _send_message_from_review(extension, review)

def _send_message_from_review(extension, review):
    rr = review.review_request
    repository = rr.repository

    target_people = [u.username for u in rr.target_people.all()]
    participants = set(p.username for p in rr.participants)

    msg = base.GenericMessage()
    msg.routing_parts.append('mozreview.review.published')

    # Try to limit this to data that:
    # * doesn't have an unbound size (like commit messages or diffs)
    # * is useful for consumers to perform quick screening based on state
    #
    # In general, we prefer consumers call the web API to get additional
    # details.
    msg.data['review_id'] = review.id
    msg.data['review_time'] = int(time.mktime(review.timestamp.utctimetuple()))
    msg.data['review_username'] = review.user.username
    msg.data['review_request_id'] = rr.id
    msg.data['review_request_bugs'] = rr.get_bug_list()
    msg.data['review_request_participants'] = sorted(participants)
    msg.data['review_request_submitter'] = rr.submitter.username
    msg.data['review_request_target_people'] = sorted(target_people)
    msg.data['repository_id'] = repository.id
    msg.data['repository_bugtracker_url'] = repository.bug_tracker
    msg.data['repository_url'] = repository.path
    # TODO consider adding participants for this specific thing (e.g. if this
    # is a reply should participants for the review being replied to).

    # TODO make work with RB localsites.
    msg.data['review_board_url'] = get_server_url()

    publish_message(extension, msg)


@if_ext_enabled
def handle_commits_published(extension=None, **kwargs):
    """Handle sending 'mozreview.commits.published'.

    This message is only sent when the parent review request, in a set of
    pushed review requests, is published with new commit information.

    This is a useful message for consumers who care about new or modified
    commits being published for review.
    """
    review_request = kwargs.get('review_request')

    if review_request is None:
        return

    commit_data = fetch_commit_data(review_request)

    if (not is_pushed(review_request, commit_data) or
            not is_parent(review_request, commit_data)):
        return

    # Check the change description and only continue if it contains a change
    # to the commit information. Currently change descriptions won't include
    # information about our extra data field, so we'll look for a change to
    # the diff which is mandatory if the commits changed. TODO: Properly use
    # the commit information once we start populating the change description
    # with it.
    #
    # A change description will not exist if this is the first publish of the
    # review request. In that case we know there must be commits since this
    # is a pushed request.
    cd = kwargs.get('changedesc')
    if (cd is not None and ('diff' not in cd.fields_changed or
                            'added' not in cd.fields_changed['diff'])):
        return

    # We publish both the review repository url as well as the landing
    # ("inbound") repository url. This gives consumers which perform hg
    # operations the option to avoid cloning the review repository, which may
    # be large.
    repo = review_request.repository
    repo_url = repo.path
    landing_repo_url = repo.extra_data.get('landing_repository_url')

    child_rrids = []
    commits = []
    ext_commits = json.loads(commit_data.extra_data.get(COMMITS_KEY, '[]'))

    for rev, rrid in ext_commits:
        child_rrids.append(int(rrid))
        commits.append({
            'rev': rev,
            'review_request_id': int(rrid),
            'diffset_revision': None
        })

    # In order to retrieve the diff revision for each commit we need to fetch
    # their correpsonding child review request.
    review_requests = dict(
        (obj.id, obj) for obj in
        ReviewRequest.objects.filter(pk__in=child_rrids))

    for commit_info in commits:
        # TODO: Every call to get_latest_diffset() makes its own query to the
        # database. It is probably possible to retrieve the diffsets we care
        # about using a single query through Django's ORM, but it's not trivial.
        commit_info['diffset_revision'] = review_requests[
            commit_info['review_request_id']
        ].get_latest_diffset().revision

    msg = base.GenericMessage()
    msg.routing_parts.append('mozreview.commits.published')
    msg.data['parent_review_request_id'] = review_request.id
    msg.data['parent_diffset_revision'] = review_request.get_latest_diffset().revision
    msg.data['commits'] = commits
    msg.data['repository_url'] = repo_url
    msg.data['landing_repository_url'] = landing_repo_url

    # TODO: Make work with RB localsites.
    msg.data['review_board_url'] = get_server_url()

    publish_message(extension, msg)


def publish_message(extension, msg):
    config = get_pulse_config(extension)
    pulse = publishers.MozReviewPublisher(**config)

    try:
        pulse.publish(msg)
    finally:
        pulse.disconnect()


def get_pulse_config(extension):
    return {
        'host': extension.get_settings('pulse_host'),
        'port': extension.get_settings('pulse_port'),
        'ssl': extension.get_settings('pulse_ssl', False),
        'user': extension.get_settings('pulse_user'),
        'password': extension.get_settings('pulse_password')
    }
