# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Mercurial extension to write replication events into Kafka."""

from __future__ import absolute_import

import os
import time

import kafka.client as kafkaclient
import vcsreplicator.producer as vcsrproducer

from mercurial.i18n import _
from mercurial import (
    cmdutil,
    commands,
    extensions,
    hg,
    util,
)

testedwith = '3.5'

cmdtable = {}
command = cmdutil.command(cmdtable)


def precommithook(ui, repo, **kwargs):
    # We could probably handle local commits. But our target audience is
    # server environments, where local commits shouldn't be happening.
    # All new changesets should be added through addchangegroup. Enforce
    # that.
    ui.warn(_('cannot commit to replicating repositories; push instead\n'))
    return True


def pretxnopenhook(ui, repo, **kwargs):
    """Verify replication log is working before starting transaction.

    It doesn't make sense to perform a lot of work only to find out that the
    replication log can not be written to. So we check replication log
    writability when we open transactions so we fail fast.
    """
    try:
        vcsrproducer.send_heartbeat(ui.replicationproducer)
    except Exception:
        ui.warn('replication log not available; all writes disabled\n')
        return 1


def changegrouphook(ui, repo, node=None, source=None, **kwargs):
    start = time.time()

    heads = set(repo.heads())
    pushnodes = []
    pushheads = []

    for rev in range(repo[node].rev(), len(repo)):
        ctx = repo[rev]

        pushnodes.append(ctx.hex())

        if ctx.node() in heads:
            pushheads.append(ctx.hex())

    vcsrproducer.record_hg_changegroup(ui.replicationproducer,
                                       repo.replicationwireprotopath,
                                       source,
                                       pushnodes,
                                       pushheads)
    duration = time.time() - start
    ui.status(_('recorded changegroup in replication log in %.3fs\n') % duration)


def initcommand(orig, ui, dest, **opts):
    # Send a heartbeat before we create the repo to ensure the replication
    # system is online. This helps guard against us creating the repo
    # and replication being offline.
    producer = ui.replicationproducer
    vcsrproducer.send_heartbeat(producer)

    res = orig(ui, dest=dest, **opts)

    # init aborts if the repo already existed or in case of error. So we
    # can only get here if we created a repo.
    path = os.path.normpath(os.path.abspath(os.path.expanduser(dest)))
    if not os.path.exists(path):
        raise util.Abort('could not find created repo at %s' % path)

    repo = hg.repository(ui, path)

    # TODO we should delete the repo if we can't write this message.
    vcsrproducer.record_new_hg_repo(producer, repo.replicationwireprotopath)
    ui.status(_('(recorded repository creation in replication log)\n'))

    return res


@command('replicatehgrc', [], 'replicate the hgrc for this repository')
def replicatehgrc(ui, repo):
    """Replicate the hgrc for this repository.

    When called, the content of the hgrc file for this repository will be
    sent to the replication service. Downstream mirrors will apply that
    hgrc.

    This command should be called when the hgrc of the repository changes.
    """
    if repo.vfs.exists('hgrc'):
        content = repo.vfs.read('hgrc')
    else:
        content = None

    producer = ui.replicationproducer
    vcsrproducer.record_hgrc_update(producer, repo.replicationwireprotopath,
                                    content)
    ui.status(_('recorded hgrc in replication log\n'))


def extsetup(ui):
    extensions.wrapcommand(commands.table, 'init', initcommand)


def uisetup(ui):
    # We assume that if the extension is loaded that we want replication
    # support enabled. Validate required config options are present.
    role = ui.config('replication', 'role')
    if not role:
        raise util.Abort('replication.role config option not set')

    if role not in ('producer', 'consumer'):
        raise util.Abort('unsupported value for replication.role: %s' % role,
                         hint='expected "producer" or "consumer"')

    section = 'replication%s' % role
    hosts = ui.configlist(section, 'hosts')
    if not hosts:
        raise util.Abort('%s.hosts config option not set' % section)
    clientid = ui.config(section, 'clientid')
    if not clientid:
        raise util.Abort('%s.clientid config option not set' % section)
    timeout = ui.configint(section, 'connecttimeout', 10)

    if role == 'producer':
        topic = ui.config(section, 'topic')
        if not topic:
            raise util.Abort('%s.topic config option not set' % section)
        partition = ui.configint(section, 'partition', -1)
        if partition == -1:
            raise util.Abort('%s.partition config option not set' % section)
        reqacks = ui.configint(section, 'reqacks', default=999)
        if reqacks not in (-1, 0, 1):
            raise util.Abort('%s.reqacks must be set to -1, 0, or 1' % section)
        acktimeout = ui.configint(section, 'acktimeout')
        if not acktimeout:
            raise util.Abort('%s.acktimeout config option not set' % section)

    class replicatingui(ui.__class__):
        @property
        def replicationproducer(self):
            if not getattr(self, '_replicationproducer', None):
                client = kafkaclient.KafkaClient(hosts, client_id=clientid,
                                                 timeout=timeout)
                self._replicationproducer = vcsrproducer.Producer(
                    client, topic, partition, batch_send=False,
                    req_acks=reqacks, ack_timeout=acktimeout)

            return self._replicationproducer

    ui.__class__ = replicatingui


def reposetup(ui, repo):
    if not repo.local():
        return

    # TODO add support for only replicating repositories under certain paths.

    ui.setconfig('hooks', 'precommit.vcsreplicator', precommithook,
                 'vcsreplicator')
    ui.setconfig('hooks', 'changegroup.vcsreplicator', changegrouphook,
                 'vcsreplicator')
    ui.setconfig('hooks', 'pretxnopen.vcsreplicator', pretxnopenhook,
                 'vcsreplicator')

    class replicatingrepo(repo.__class__):
        @property
        def replicationwireprotopath(self):
            """Return the path to this repo as it is represented over wire.

            By setting entries in the [replicationpathrewrites] section, you can
            effectively normalize filesystem paths to a portable representation.
            For example, say all Mercurial repositories are stored in
            ``/repos/hg/`` on the local filesystem. You could create a rewrite
            rule as follows:

               [replicationpathrewrites]
               /repos/hg/ = {hg}/

            Over the wire, a local path such as ``/repos/hg/my-repo`` would be
            normalized to ``{hg}/my-repo``.

            Then on the consumer, you could design the opposite to map the
            repository to a different filesystem path. e.g.:

               [replicationpathrewrites]
               {hg}/ = /mirror/hg/

            The consumer would then expand the path to ``/mirror/hg/my-repo``.

            Matches are case insensitive but rewrites are case preserving.
            """
            lower = self.root.lower()
            for source, dest in self.ui.configitems('replicationpathrewrites'):
                if lower.startswith(source):
                    return dest + self.root[len(source):]

            return self.root

    repo.__class__ = replicatingrepo
