from setuptools import setup, find_packages

setup(
    name='mozvcssync',
    version='0.1',
    description='Synchronize changes across VCS repositories',
    url='https://mozilla-version-control-tools.readthedocs.io/',
    author='Mozilla',
    author_email='dev-version-control@lists.mozilla.org',
    license='MPL 2.0',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 2.7',
    ],
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'linearize-git=mozvcssync.cli:linearize_git',
            'linearize-git-to-hg=mozvcssync.cli:linearize_git_to_hg',
        ],
    },
    install_requires=[
        'dulwich>=0.16',
        'github3.py>=0.9.6',
        'kombu>=3.0.37',
        'Mercurial>=4.0',
    ],
)
