#!/usr/bin/env python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import datetime
import sys

def totals_by_day(fh):
    days = {}

    for line in fh:
        parts = line.rstrip().split()

        try:
            when, repo, ip, command, size, t_wall, t_cpu = parts
            try:
                when = datetime.datetime.strptime(when, '%Y-%m-%dT%H:%M:%S.%f')
            except ValueError:
                when = datetime.datetime.strptime(when, '%Y-%m-%dT%H:%M:%S')
        except (TypeError, ValueError):
            continue

        size = int(size)
        t_wall = float(t_wall)
        t_cpu = float(t_cpu)

        date = when.date()

        totals = days.setdefault(date, [0, 0, 0.0, 0.0])
        totals[0] += 1
        totals[1] += size
        totals[2] += t_wall
        totals[3] += t_cpu

    for date, totals in sorted(days.items()):
        print('%s\t%d\t%d\t%d\t%d' % (date.isoformat(), totals[0], totals[1],
                                      totals[2], totals[3]))

if __name__ == '__main__':
    totals_by_day(sys.stdin)
