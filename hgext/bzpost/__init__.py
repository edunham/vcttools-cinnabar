# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Post changeset URLs to Bugzilla.

This extension will post the URLs of pushed changesets to Bugzilla
automatically.

To use, activate this extension by adding the following to your
hgrc:

    [extensions]
    bzpost = /path/to/version-control-tools/hgext/bzpost

You will also want to define your Bugzilla credentials in your hgrc to
avoid prompting:

    [bugzilla]
    username = foo@example.com
    password = password

After successfully pushing to a known Firefox repository, this extension
will add a comment to the first referenced bug in all pushed changesets
containing the URLs of the pushed changesets.

Limitations
===========

We currently only post comments to integration/non-release repositories.
This is because pushes to release repositories involve updating other
Bugzilla fields. This extension could support these someday - it just
doesn't yet.

We only support posting comments to known Firefox repositories. This
extension could be adopted to post URLs to non-Firefox repositories
as well.
"""

import os

from mercurial import demandimport
from mercurial import exchange
from mercurial import extensions
from mercurial.i18n import _

OUR_DIR = os.path.dirname(__file__)
execfile(os.path.join(OUR_DIR, '..', 'bootstrap.py'))

# requests doesn't like lazy module loading.
demandimport.disable()
from bugsy import Bugsy
demandimport.enable()
from mozautomation.commitparser import parse_bugs
from mozautomation.repository import (
    RELEASE_TREES,
    resolve_trees_to_uris,
    resolve_uri_to_tree,
)
from mozhg.auth import getbugzillaauth

testedwith = '3.0.1'

def wrappedpushbookmark(orig, pushop):
    result = orig(pushop)

    tree = resolve_uri_to_tree(pushop.remote.url())
    if not tree:
        return result

    # We don't support release trees (yet) because they have special flags
    # that need to get updated.
    if tree in RELEASE_TREES:
        return result

    baseuri = resolve_trees_to_uris([tree])[0][1]
    assert baseuri

    bugsmap = {}

    for node in pushop.outgoing.missing:
        ctx = pushop.repo[node]

        # Don't do merge commits.
        if len(ctx.parents()) > 1:
            continue

        # Our bug parser is buggy for Gaia bump commit messages.
        if '<release+b2gbumper@mozilla.com>' in ctx.user():
            continue

        bugs = parse_bugs(ctx.description())

        if not bugs:
            continue

        bugsmap.setdefault(bugs[0], []).append(ctx.hex()[0:12])

    if not bugsmap:
        return result

    ui = pushop.ui
    bzauth = getbugzillaauth(ui)
    if not bzauth or not bzauth.username or not bzauth.password:
        return result

    bugsy = Bugsy(username=bzauth.username, password=bzauth.password)

    for bugnumber, nodes in bugsmap.items():
        bug = bugsy.get(bugnumber)

        comments = bug.get_comments()
        missing_nodes = []

        # When testing whether this changeset URL is referenced in a
        # comment, we only need to test for the node fragment. The
        # important side-effect is that each unique node for a changeset
        # is recorded in the bug.
        for node in nodes:
            if not any(node in comment.text for comment in comments):
                missing_nodes.append(node)

        if not missing_nodes:
            ui.write(_('bug %s already knows about pushed changesets\n') %
                bugnumber)
            continue

        lines = ['%s/rev/%s' % (baseuri, node) for node in missing_nodes]

        comment = '\n'.join(lines)

        ui.write(_('recording push in bug %s\n') % bugnumber)
        bug.add_comment(comment)

    return result

def extsetup(ui):
    extensions.wrapfunction(exchange, '_pushbookmark', wrappedpushbookmark)
