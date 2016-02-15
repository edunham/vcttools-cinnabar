#!/usr/bin/env python
from reviewboard.extensions.packaging import setup
from setuptools import find_packages

from mozreview import get_package_version


setup(
    name='mozreview',
    version=get_package_version(),
    license='MIT',
    description='MozReview extension to Review Board',
    packages=find_packages(),
    install_requires=[
        'MozillaPulse',
        'mozautomation>=0.2'
    ],
    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
    ],
    entry_points={
        'reviewboard.extensions':
            '%s = mozreview.extension:MozReviewExtension' % 'mozreview',
    },
    package_data={
        'mozreview': [
            'templates/**/*.html',
            'static/**/*.css',
            'static/**/*.less',
            'static/**/*.js'
        ]
    }
)
