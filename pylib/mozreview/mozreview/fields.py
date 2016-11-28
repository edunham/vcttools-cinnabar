from __future__ import unicode_literals

import json
import logging

from django.template.defaultfilters import linebreaksbr
from django.template.loader import Context, get_template
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext as _

from reviewboard.extensions.base import get_extension_manager
from reviewboard.reviews.fields import BaseReviewRequestField
from reviewboard.reviews.models import ReviewRequest, ReviewRequestDraft

from mozreview.autoland.models import AutolandEventLogEntry, AutolandRequest
from mozreview.extra_data import (
    AUTHOR_KEY,
    BASE_COMMIT_KEY,
    COMMIT_ID_KEY,
    COMMITS_KEY,
    fetch_commit_data,
    gen_child_rrs,
    get_parent_rr,
    is_parent,
    is_pushed,
    REVIEWER_MAP_KEY,
)
from mozreview.file_diff_reviewer.models import FileDiffReviewer


logger = logging.getLogger(__name__)


def ensure_review_request(review_request_details):
    if isinstance(review_request_details, ReviewRequestDraft):
        review_request_details = review_request_details.get_review_request()

    return review_request_details


class CommitDataBackedField(BaseReviewRequestField):
    """Base class field backed by CommitData rather then built-in extra_data.

    This Field class will emulate the behavior of normal review
    request fields but stores its data in CommitData.extra_data
    and CommitData.draft_extra_data instead of the built-in
    extra_data fields on ReviewRequest and ReviewRequestDraft.

    Unlike built-in extra data fields, these values will not be
    automatically copied from draft_extra_data to extra_data. If
    that behaviour is desired, the field id should be added to
    DRAFTED_EXTRA_DATA_KEYS (defined in extra_data.py).
    """

    def load_value(self, review_request_details):
        # This must use a CommitData for ``review_request_details`` instead
        # of the one stored in ``self.commit_data``. See comment on
        # BaseReviewRequestField
        commit_data = fetch_commit_data(review_request_details)
        return commit_data.get_for(review_request_details, self.field_id)

    def save_value(self, value):
        commit_data = fetch_commit_data(self.review_request_details)
        commit_data.set_for(self.review_request_details, self.field_id, value)
        commit_data.save(update_fields=['extra_data', 'draft_extra_data'])


class CombinedReviewersField(BaseReviewRequestField):
    """ This field allows for empty pushes on the parent request"""
    field_id = REVIEWER_MAP_KEY
    is_editable = True
    can_record_change_entry = True

    def should_render(self, value):
        return False

    def get_change_entry_sections_html(self, info):
        return [{
            'title': 'Reviewers',
            'rendered_html': 'List updated',
        }]


class CommitsListField(CommitDataBackedField):
    """The commits list field for review requests.

    This field is injected in the details of a review request that
    is a "push" based review request.
    """
    field_id = COMMITS_KEY
    label = ""

    can_record_change_entry = True

    def should_render(self, value):
        return False

    def has_value_changed(self, old_value, new_value):
        # Just to be safe, we de-serialize the json and compare values
        if old_value is not None and new_value is not None:
            return json.loads(old_value) != json.loads(new_value)
        return old_value != new_value

    def render_change_entry_html(self, info):
        old_value = json.loads(info.get('old', ['[]'])[0])
        old_commits = [c for c, r in old_value]

        new_value = json.loads(info.get('new', ['[]'])[0])
        new_commits = [c for c, r in new_value]

        # Pad the commit lists so they're equal in length.
        max_len = max(len(old_commits), len(new_commits))
        old_commits += [''] * (max_len - len(old_commits))
        new_commits += [''] * (max_len - len(new_commits))

        commit_changes = zip(old_commits, new_commits)

        review_request = self.review_request_details.get_review_request()

        return get_template('mozreview/commits_changedescription.html').render(
            Context({
                'commit_changes': commit_changes,
                'review_request': review_request,
                'repository_path': review_request.repository.path
            }))


class CommitDetailField(BaseReviewRequestField):
    """This field provides the main content for review requests"""
    label = ""
    field_id = "p2rb.CommitDetail"

    def should_render(self, value):
        return is_pushed(self.review_request_details)

    def as_html(self):
        user = self.request.user

        commit_data = fetch_commit_data(self.review_request_details)
        commit_id = commit_data.get_for(self.review_request_details, COMMIT_ID_KEY)

        review_request = self.review_request_details.get_review_request()
        parent = get_parent_rr(review_request)
        parent_details = parent.get_draft(user) or parent

        author = commit_data.extra_data.get(AUTHOR_KEY, None)

        # If a user can view the parent draft they should also have
        # permission to view every child. We check if the child is
        # accessible anyways in case it has been restricted for other
        # reasons.
        children_details = [
            child for child in gen_child_rrs(parent_details, user=user)
            if child.is_accessible_by(user)]

        # Generate the import and pull input field contents
        import_text = pull_text = ""
        repo_path = review_request.repository.path

        if commit_id:
            import_text = "hg import %s/rev/%s" % (repo_path, commit_id)

        last_child_commit_id = commit_id
        if is_parent(self.review_request_details, commit_data=commit_data):
            last_child_commit_data = fetch_commit_data(children_details[-1])
            last_child_commit_id = (
                last_child_commit_data.extra_data.get(COMMIT_ID_KEY))

        pull_text = "hg pull -r %s %s" % (last_child_commit_id, repo_path)

        # Get just the extended commit message details for display
        commit_message_detail = "\n".join(
            self.review_request_details.description.splitlines()[1:]).strip()

        return get_template('mozreview/commit-main.html').render(Context({
            'review_request_details': self.review_request_details,
            'parent_details': parent_details,
            'user': user,
            'author': author,
            'pull_text': pull_text,
            'import_text': import_text,
            'commit_message_detail': commit_message_detail,
        }))


class CommitAuthorField(CommitDataBackedField):
    """Field for the author of the review request's commit"""
    field_id = AUTHOR_KEY
    label = _("Author")

    def should_render(self, value):
        # Only show this for child review requests as for parent review
        # requests different constituent commits can have different authors.
        # Also, do not show it if it's empty, because review requests created
        # before the author field was introduced will not have this information.
        return not is_parent(self.review_request_details) and value

class BaseCommitField(CommitDataBackedField):
    """Field for the commit a review request is based on.

    This field stores the base commit that a parent review request is
    based on (the parent commit of the first commit in the series).

    A change in this value indicates that the review request series
    has been rebased or some of the commits in the request have been
    landed/submitted.
    """
    field_id = BASE_COMMIT_KEY
    label = _("Base Commit")
    can_record_change_entry = True

    def should_render(self, value):
        return (is_pushed(self.review_request_details) and
                is_parent(self.review_request_details) and
                False) # TODO: Remove and render hg web link to the base commit.

    def get_change_entry_sections_html(self, info):
        """Render changes in the base commit as rebases."""
        old = info.get('old', [None])[0]
        new = info.get('new', [None])[0]

        if old is None or new is None:
            # We should always have an old base commit and a new
            # base commit, except for review requests which were
            # in flight when this field landed. We'll ignore this
            # case as it should be rare.
            return []

        # TODO: When we start partially landing commit series the
        # base commit may change to one of the landed commits
        # meaning we'd have a difference here but it wasn't actually
        # a rebase.
        return [{
            'title': 'Rebase',
            'rendered_html': mark_safe(self.render_change_entry_html(info)),
        }]

    def render_change_entry_html(self, info):
        """Render the change of base commit as a rebase."""
        old_value = info['old'][0]
        new_value = info['new'][0]
        repo_path = self._get_repo_path()

        return get_template('mozreview/changedesc-rebase.html').render(Context({
            'old_base': old_value,
            'new_base': new_value,
            'repo_path': repo_path,
        }))

    def _get_repo_path(self):
        """Retrieve the path for the repository associated with this request."""
        review_request = self.review_request_details.get_review_request()
        return review_request.repository.path.rstrip('/')


class TryField(BaseReviewRequestField):
    """The field for kicking off Try builds and showing Try state.

    This field allows a user to kick off a Try build for each unique
    revision. Once kicked off, it shows the state of the most recent
    Try build.
    """
    field_id = 'p2rb.autoland_try'
    label = _('Try')

    can_record_change_entry = True

    _retrieve_error_txt = _('There was an error retrieving the try push.')
    _waiting_txt = _('Waiting for the autoland to try request to execute, '
                     'hold tight. If the try tree is closed autoland will '
                     'retry your push for you until the tree opens.')
    _autoland_problem = _('Autoland reported a problem: %s')
    _job_url = 'https://treeherder.mozilla.org/#/jobs?repo=try&revision=%s'

    def should_render(self, value):
        return False

    def load_value(self, review_request_details):
        return review_request_details.extra_data.get('p2rb.autoland_try')

    def get_change_entry_sections_html(self, info):
        if 'new' not in info:
            # If there was no new value we won't bother rendering anything.
            # this would really only happen if the latest try build was
            # removed and not replaced with a new one, which would be very
            # strange.
            return []

        return [{
            'title': self.label,
            'rendered_html': mark_safe(self.render_change_entry_html(info)),
        }]

    def render_change_entry_html(self, info):
        try:
            autoland_id = int(info['new'][0])
        except (ValueError, TypeError):
            # Something unexpected was recorded as the autoland id in the
            # changedescription. This either means we have a serious bug or
            # someone was attempting to change the field themselves (possibly
            # maliciously).
            logger.error('A malformed autoland_id was detected: %s' %
                         info['new'][0])
            return self._retrieve_error_txt

        try:
            ar = AutolandRequest.objects.get(pk=autoland_id)
        except:
            logger.error('An unknown autoland_id was detected: %s' %
                info['new'][0])
            return self._retrieve_error_txt

        if ar.last_known_status == AutolandEventLogEntry.REQUESTED:
            return self._waiting_txt
        elif ar.last_known_status == AutolandEventLogEntry.PROBLEM:
            return linebreaksbr(self._autoland_problem % ar.last_error_msg)
        elif ar.last_known_status == AutolandEventLogEntry.SERVED:
            url = self._job_url % ar.repository_revision
            template = get_template('mozreview/try_result.html')
            return template.render(Context({'url': url}))
        else:
            return linebreaksbr(self._retrieve_error_txt)


class FileDiffReviewerField(BaseReviewRequestField):
    """This field initializes a FileDiffReviewer collection.

    Create the collection of FileDiffReviewer for this specific user/review if
    not present.
    """
    # RB validation requires this to be unique, so we fake a field id
    field_id = "p2rb.FileDiffReviewerField"
    label = ""

    def as_html(self):
        user = self.request.user
        file_diff_reviewer_list = []
        reviewer_ids = self.review_request_details.target_people.values_list(
            'id', flat=True
        )

        if (user.is_authenticated() and
                isinstance(self.review_request_details, ReviewRequest)):
            diffsets = self.review_request_details.get_diffsets()
            # Merge all the FileDiffs together
            files = sum([list(diff.files.all()) for diff in diffsets], [])

            for item in files:
                file_diff_reviewer, _ = FileDiffReviewer.objects.get_or_create(
                    reviewer_id=user.id,
                    file_diff_id=item.id
                )
                file_diff_reviewer_list.append({
                    'id': file_diff_reviewer.id,
                    'reviewer_id': file_diff_reviewer.reviewer_id,
                    'file_diff_id': file_diff_reviewer.file_diff_id,
                    'last_modified': file_diff_reviewer.last_modified,
                    'reviewed': file_diff_reviewer.reviewed
                })

        return get_template('mozreview/file_diff_reviewer_data.html').render(
            Context({'file_diff_reviewer_list': file_diff_reviewer_list})
        )
