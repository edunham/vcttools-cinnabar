#!/usr/bin/env python
import os
from mercurial.node import short


def hook(ui, repo, node, hooktype, source=None, **kwargs):
    if source in ('pull', 'strip'):
        return 0

    root = ui.config('hgmo', 'repo_root', '/repo/hg/mozilla')

    if not repo.root.startswith(root):
        return 0

    repo_name = repo.root[len(root) + 1:]

    # All changesets from node to "tip" inclusive are part of this push.
    rev = repo.changectx(node).rev()
    tip = repo.changectx('tip').rev()
    tip_node = short(repo.changectx(tip).node())

    num_changes = tip + 1 - rev
    url = 'https://hg.mozilla.org/%s/' % repo_name

    if num_changes <= 10:
        plural = 's' if num_changes > 1 else ''
        ui.write('\nView your change%s here:\n' % plural)

        for i in xrange(rev, tip + 1):
            node = short(repo.changectx(i).node())
            ui.write('  %srev/%s\n' % (url, node))
    else:
        ui.write('\nView the pushlog for these changes here:\n')
        ui.write('  %spushloghtml?changeset=%s\n' % (url, tip_node))

    # For repositories that report CI results to Treeherder, also output a
    # Treeherder url.
    treeherder_repo = ui.config('mozilla', 'treeherder_repo')
    if treeherder_repo:
        treeherder_base_url = 'https://treeherder.mozilla.org'
        ui.write('\nFollow the progress of your build on Treeherder:\n')
        ui.write('  %s/#/jobs?repo=%s&revision=%s\n' % (treeherder_base_url,
                                                        treeherder_repo,
                                                        tip_node))
        # if specifying a try build and talos jobs are enabled, suggest that
        # user use compareperf
        if treeherder_repo == 'try':
            msg = repo.changectx(tip).description()
            if ((' -t ' in msg or ' --talos ' in msg) and '-t none' not in msg
                and '--talos none' not in msg):
                ui.write('\nIt looks like this try push has talos jobs. Compare '
                       'performance against a baseline revision:\n')
                ui.write('  %s/perf.html#/comparechooser'
                       '?newProject=try&newRevision=%s\n' % (
                           treeherder_base_url, tip_node))
    return 0
