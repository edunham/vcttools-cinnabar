#!/usr/bin/env python2.7
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Find Mercurial repositories under a specified path."""

import argparse
import grp
import os
import sys


def find_hg_repos(path):
    for root, dirs, files in os.walk(path):
        for d in dirs:
            if d == '.hg':
                yield root

        dirs[:] = [d for d in dirs if d != '.hg']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', help='Group owner to search for')
    parser.add_argument('paths', nargs='+')

    args = parser.parse_args()

    gid = None
    if args.group:
        try:
            group = grp.getgrnam(args.group)
            gid = group[2]
        except KeyError:
            print('group %s is not known' % args.group)
            sys.exit(1)

    for d in args.paths:
        for path in find_hg_repos(d):
            if gid is not None:
                st = os.stat(path)
                if st.st_gid != gid:
                    continue

            print(path[len(d):])
