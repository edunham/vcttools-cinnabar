# rbmozui Extension for Review Board.

from __future__ import unicode_literals

from django.conf import settings
from django.conf.urls import include, patterns, url

from reviewboard.extensions.base import Extension
from reviewboard.extensions.hooks import (TemplateHook, URLHook)
from reviewboard.reviews.builtin_fields import (TargetPeopleField,
                                                TestingDoneField)
from reviewboard.reviews.fields import (get_review_request_field,
                                        get_review_request_fieldset)
from reviewboard.urls import (diffviewer_url_names,
                              review_request_url_names)


class RBMozUI(Extension):
    metadata = {
        'Name': 'rbmozui',
        'Summary': 'UI tweaks to Review Board for Mozilla',
    }
    css_bundles = {
        'commits': {
            'source_filenames': ['css/commits.less'],
        },
        'default': {
            'source_filenames': ['css/common.css'],
        },
        'review': {
            'source_filenames': ['css/review.less'],
        },
        'viewdiff': {
            'source_filenames': ['css/viewdiff.less'],
        },
    }
    js_bundles = {
        'commits': {
            'source_filenames': ['js/commits.js'],
        },
        'review': {
            'source_filenames': ['js/review.js'],
        }
    }

    def initialize(self):
        # Start by hiding the Testing Done field in all review requests, since
        # Mozilla developers will not be using it.
        main_fieldset = get_review_request_fieldset('main')
        testing_done_field = get_review_request_field('testing_done')
        if testing_done_field:
            main_fieldset.remove_field(testing_done_field)

        reviewers_fieldset = get_review_request_fieldset('reviewers')
        people_field = get_review_request_field('target_people')
        if people_field:
            reviewers_fieldset.remove_field(people_field)

        # All of our review request styling is injected via review-stylings-css,
        # which in turn loads the review.css static bundle.
        TemplateHook(self, 'base-css', 'rbmozui/commits-stylings-css.html',
                     apply_to='rbmozui-commits')
        TemplateHook(self, 'base-css', 'rbmozui/review-stylings-css.html',
                     apply_to=review_request_url_names + ['rbmozui-commits'])
        TemplateHook(self, 'base-css', 'rbmozui/viewdiff-stylings-css.html',
                     apply_to=diffviewer_url_names)
        TemplateHook(self, 'base-before-content', 'rbmozui/review-header.html',
                     apply_to=review_request_url_names + ['rbmozui-commits'])
        TemplateHook(self, 'base-scripts-post', 'rbmozui/review-scripts-js.html',
                     apply_to=review_request_url_names)
        TemplateHook(self, 'rbmozui-commits-scripts', 'rbmozui/commits-scripts-js.html',
                     apply_to='rbmozui-commits')
        urlpatterns = patterns('',
            url(r'^rbmozui/', include('rbmozui.urls')))
        URLHook(self, urlpatterns)

    def shutdown(self):
        # We have to put the TestingDone field back before we shut down
        # in order to get the instance back to its original state.
        main_fieldset = get_review_request_fieldset('main')
        testing_done_field = get_review_request_field('testing_done')
        if not testing_done_field:
            main_fieldset.add_field(TestingDoneField)

        reviewers_fieldset = get_review_request_fieldset('reviewers')
        people_field = get_review_request_field('target_people')
        if not people_field:
            reviewers_fieldset.add_field(TargetPeopleField)

        super(RBMozUI, self).shutdown()
