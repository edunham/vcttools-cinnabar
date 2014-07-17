# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""work with Firefox source repositories more easily.

The Firefox source repository is a collection of multiple repositories
all stemming from the same root commit. They can effectively be modeled
as a single, unified repository with multiple heads. This extension
facilitates doing that.

Remote Tracking Tags
====================

When you pull from a known Firefox repository, this extension will
automatically create a local-only tag corresponding to the name of
the remote repository.

For example, when you pull from https://hg.mozilla.org/mozilla-central,
the ``central`` tag will be created. You can then update to the last
pull mozilla-central changeset by running ``hg up central``.

These local tags are read only. If you update to a tag and commit, the
tag will not move forward.
"""

import os

from mercurial import (
    cmdutil,
    extensions,
    hg,
)
from mercurial.error import RepoError
from mercurial.i18n import _

OUR_DIR = os.path.dirname(__file__)
execfile(os.path.join(OUR_DIR, '..', 'bootstrap.py'))

from mozautomation.repository import (
    resolve_trees_to_uris,
    resolve_uri_to_tree,
)

testedwith = '3.0 3.0.1 3.0.2'

cmdtable = {}
command = cmdutil.command(cmdtable)

shorttemplate = ''.join([
    '{label("log.changeset", rev)}',
    '{label("log.changeset", ":")}',
    '{label("log.changeset", node|short)}',
    ' ',
    '{label("log.tag", tags)}',
    ' ',
    '{label("log.summary", firstline(desc))}',
    '\n',
    ])

# Wrap repo lookup to automagically resolve tree names to URIs.
def peerorrepo(orig, ui, path, *args, **kwargs):
    try:
        return orig(ui, path, *args, **kwargs)
    except RepoError:
        tree, uri = resolve_trees_to_uris([path])[0]
        if not uri:
            raise

        return orig(ui, uri, *args, **kwargs)

@command('fxheads', [
    ('T', 'template', shorttemplate,
     _('display with template'), _('TEMPLATE')),
    ], _('show Firefox tree heads'))
def fxheads(ui, repo, **opts):
    """Show last known head commits for pulled Firefox trees.

    The displayed list may be out of date. Pull before running to ensure
    data is current.
    """
    displayer = cmdutil.show_changeset(ui, repo, opts)
    for tag, node in sorted(repo.tags().items()):
        if not resolve_trees_to_uris([tag])[0][1]:
            continue

        ctx = repo[node]
        displayer.show(ctx)

    displayer.close()

def extsetup(ui):
    extensions.wrapfunction(hg, '_peerorrepo', peerorrepo)

def reposetup(ui, repo):
    if not repo.local():
        return

    orig_pull = repo.pull
    class firefoxtreerepo(repo.__class__):
        def pull(self, remote, *args, **kwargs):
            old_rev = len(self)
            res = orig_pull(remote, *args, **kwargs)
            tree = resolve_uri_to_tree(remote.url())
            if tree:
                tree = tree.encode('utf-8')
                self._updateremoterefs(remote, tree)

            return res

        def _updateremoterefs(self, remote, tree):
            # We only care about the default branch. We could import
            # RELBRANCH and other branches if we really cared about it.
            # Maybe later.
            branchmap = remote.branchmap()
            if 'default' not in branchmap:
                return

            # Firefox repos should only ever have a single head in the
            # default branch.
            defaultnodes = branchmap['default']
            node = defaultnodes[-1]
            self.tag(tree, node, message=None, local=True, user=None, date=None)

    repo.__class__ = firefoxtreerepo
