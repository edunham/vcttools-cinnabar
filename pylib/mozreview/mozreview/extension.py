from __future__ import unicode_literals

import logging

from django.conf.urls import include, patterns, url
from django.db.models.signals import post_save

from djblets.webapi.resources import (register_resource_for_model,
                                      unregister_resource_for_model)
from reviewboard.extensions.base import Extension
from reviewboard.extensions.hooks import (HeaderDropdownActionHook,
                                          ReviewRequestFieldsHook,
                                          SignalHook,
                                          TemplateHook,
                                          URLHook)
from reviewboard.reviews.builtin_fields import TestingDoneField
from reviewboard.reviews.fields import (get_review_request_field,
                                        get_review_request_fieldset)
from reviewboard.reviews.models import ReviewRequestDraft
from reviewboard.urls import (diffviewer_url_names,
                              review_request_url_names)

from mozreview.autoland.models import (AutolandRequest,
                                       ImportPullRequestRequest)
from mozreview.autoland.resources import (autoland_request_update_resource,
                                          import_pullrequest_trigger_resource,
                                          import_pullrequest_update_resource,
                                          try_autoland_trigger_resource)
from mozreview.autoland.views import import_pullrequest
from mozreview.batchreview.resources import batch_review_resource
from mozreview.extra_data import (get_parent_rr, is_parent, is_pushed,
                                  update_parent_rr_reviewers)
from mozreview.fields import (BaseCommitField,
                              CombinedReviewersField,
                              CommitsListField,
                              TryField)
from mozreview.hooks import MozReviewApprovalHook
from mozreview.ldap.resources import ldap_association_resource
from mozreview.middleware import MozReviewUserProfileMiddleware
from mozreview.pulse import initialize_pulse_handlers
from mozreview.resources.review_request_summary import (
    review_request_summary_resource,)


class MozReviewExtension(Extension):
    metadata = {
        'Name': 'mozreview',
        'Summary': 'MozReview extension to Review Board',
    }

    default_settings = {
        'enabled': False,
        'pulse_host': '',
        'pulse_port': '',
        'pulse_user': '',
        'pulse_password': '',
        'pulse_ssl': False,
        'autoland_try_ui_enabled': False,
        'autoland_url': '',
        'autoland_user': '',
        'autoland_password': '',
        'autoland_testing': False,
        'autoland_import_pullrequest_ui_enabled': False,
        'ldap_url': '',
        'ldap_user': '',
        'ldap_password': '',
    }

    is_configurable = True

    css_bundles = {
        'review': {
            'source_filenames': ['mozreview/css/review.less',
                                 'mozreview/css/commits.less'],
        },
        'viewdiff': {
            'source_filenames': ['mozreview/css/viewdiff.less'],
        },
    }
    js_bundles = {
        'reviews': {
            #TODO: Everything will break if common.js is not first in this list
            'source_filenames': ['mozreview/js/common.js',
                                 'mozreview/js/commits.js',
                                 'mozreview/js/review.js',
                                 'mozreview/js/try.js',
                                 'mozreview/js/ui.mozreviewautocomplete.js',]
        },
        'import-pullrequest': {
            'source_filenames': ['mozreview/js/import-pullrequest.js',],
            'apply_to': ['import_pullrequest',],
        },
    }

    resources = [
        autoland_request_update_resource,
        batch_review_resource,
        ldap_association_resource,
        review_request_summary_resource,
        try_autoland_trigger_resource,
        import_pullrequest_trigger_resource,
        import_pullrequest_update_resource,
    ]

    middleware = [
        MozReviewUserProfileMiddleware,
    ]

    def initialize(self):
        register_resource_for_model(AutolandRequest,
                                    try_autoland_trigger_resource)
        register_resource_for_model(ImportPullRequestRequest,
                                    import_pullrequest_trigger_resource)
        initialize_pulse_handlers(self)

        URLHook(self,
                patterns('', url(r'^mozreview/', include('mozreview.urls'))))

        HeaderDropdownActionHook(self, actions=[{
            'label': 'MozReview',
            'items': [
                {
                    'label': 'User Guide',
                    'url': 'https://mozilla-version-control-tools.readthedocs.org/en/latest/mozreview-user.html',
                },
                {
                    'label': 'Mercurial for Mozillians',
                    'url': 'https://mozilla-version-control-tools.readthedocs.org/en/latest/hgmozilla/index.html',
                },
                {
                    'label': 'Hacking MozReview',
                    'url': 'https://mozilla-version-control-tools.readthedocs.org/en/latest/hacking-mozreview.html',
                },
                {
                    'label': 'File a Bug',
                    'url': 'https://bugzilla.mozilla.org/enter_bug.cgi?product=Developer%20Services&component=MozReview',
                },
            ],
        }])

        # Start by hiding the Testing Done field in all review requests,
        # since Mozilla developers will not be using it.
        main_fieldset = get_review_request_fieldset('main')
        testing_done_field = get_review_request_field('testing_done')
        if testing_done_field:
            main_fieldset.remove_field(testing_done_field)

        # All of our review request styling is injected via
        # review-stylings-css, which in turn loads the review.css static
        # bundle.
        TemplateHook(self, 'base-css', 'mozreview/review-stylings-css.html',
                     apply_to=review_request_url_names)
        TemplateHook(self, 'base-css', 'mozreview/viewdiff-stylings-css.html',
                     apply_to=diffviewer_url_names)
        TemplateHook(self, 'base-scripts-post',
                     'mozreview/review-scripts-js.html',
                     apply_to=review_request_url_names)

        ReviewRequestFieldsHook(self, 'main', [CommitsListField])
        # This forces the Commits field to be the top item.
        main_fieldset.field_classes.insert(0,
                                           main_fieldset.field_classes.pop())

        # The above hack forced Commits at the top, but the rest of these
        # fields are fine below the Description.
        ReviewRequestFieldsHook(self, 'main', [CombinedReviewersField])
        ReviewRequestFieldsHook(self, 'main', [TryField])
        ReviewRequestFieldsHook(self, 'main', [BaseCommitField])

        # Use a custom method to calculate a review approval state.
        MozReviewApprovalHook(self)

        SignalHook(self, post_save, self.on_draft_changed,
                   sender=ReviewRequestDraft)


        URLHook(self, patterns('',
            url(r'^import-pullrequest/(?P<user>.+)/(?P<repo>.+)/(?P<pullrequest>\d+)/$',
            import_pullrequest, name='import_pullrequest')))

    def shutdown(self):
        # We have to put the TestingDone field back before we shut down
        # in order to get the instance back to its original state.
        main_fieldset = get_review_request_fieldset('main')
        testing_done_field = get_review_request_field('testing_done')
        if not testing_done_field:
            main_fieldset.add_field(TestingDoneField)

        unregister_resource_for_model(AutolandRequest)
        super(MozReviewExtension, self).shutdown()


    def on_draft_changed(self, sender, **kwargs):
        instance = kwargs["instance"]
        rr = instance.get_review_request()

        if is_pushed(instance) and not is_parent(rr):
            parent_rr = get_parent_rr(rr)
            parent_rr_draft = parent_rr.get_draft()

            if parent_rr_draft is None:
                parent_rr_draft = ReviewRequestDraft.create(parent_rr)

            update_parent_rr_reviewers(parent_rr_draft)
