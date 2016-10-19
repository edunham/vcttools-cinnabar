#require hgmodocker

  $ . $TESTDIR/pylib/vcsreplicator/tests/helpers.sh
  $ vcsrenv

Create a repository

  $ hgmo create-repo mozilla-central scm_level_1
  (recorded repository creation in replication log)
  $ standarduser

The aggregate topic should contain a heartbeat and repo creation message

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini
  $ hgmo exec hgweb1 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ paconsumer --dump
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 0
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    generaldelta: false
    name: hg-repo-init-2
    path: '{moz}/mozilla-central'

  $ hg -q clone ssh://${SSH_SERVER}:${SSH_PORT}/mozilla-central
  $ cd mozilla-central

  $ touch foo
  $ hg -q commit -A -m initial
  $ hg push
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: recorded push in pushlog
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/77538e1ce4bec5f7aac58a7ceca2da0e38e90a72
  remote: recorded changegroup in replication log in \d+\.\d+s (re)

The aggregate topic should contain a changegroup message

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini
  $ hgmo exec hgweb1 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini
  $ sleep 2

  $ paconsumer --dump --start-from 2
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    heads:
    - 77538e1ce4bec5f7aac58a7ceca2da0e38e90a72
    name: hg-changegroup-2
    nodecount: 1
    path: '{moz}/mozilla-central'
    source: serve

Stopping the replication on an active mirror should result in no message copy

  $ hgmo exec hgweb0 /usr/bin/supervisorctl stop vcsreplicator:2
  vcsreplicator:2: stopped

  $ echo lag > foo
  $ hg commit -m 'push with mirror stopped'
  $ hg push
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: recorded push in pushlog
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/8f2fa335d20b56ae20f663553e7e94e4ccdda8ed
  remote: recorded changegroup in replication log in \d+\.\d+s (re)

  $ hgmo exec hgweb1 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ paconsumer --dump --start-from 6
  No handlers could be found for logger "kafka.consumer.simple"
  []

Starting the replication consumer should result in the message being written

  $ hgmo exec hgweb0 /usr/bin/supervisorctl start vcsreplicator:2
  vcsreplicator:2: started
  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini
  $ paconsumer --dump --start-from 6
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    heads:
    - 8f2fa335d20b56ae20f663553e7e94e4ccdda8ed
    name: hg-changegroup-2
    nodecount: 1
    path: '{moz}/mozilla-central'
    source: serve

Aggregation of messages from multiple partitions works

  $ hgmo exec hgweb0 /usr/bin/supervisorctl stop vcsreplicator:*
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)
  vcsreplicator:\d: stopped (re)

  $ hgmo create-repo mc2 scm_level_1
  (recorded repository creation in replication log)
  $ hgmo create-repo try scm_level_1
  (recorded repository creation in replication log)
  $ hgmo create-repo users/foo scm_level_1
  (recorded repository creation in replication log)

  $ hgmo exec hgweb1 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ hgmo exec hgweb0 /usr/bin/supervisorctl start vcsreplicator:*
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)
  vcsreplicator:\d: started (re)

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ paconsumer --dump --start-from 8
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 0
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 0
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 0
    name: heartbeat-1
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 2
    generaldelta: false
    name: hg-repo-init-2
    path: '{moz}/mc2'
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 4
    generaldelta: false
    name: hg-repo-init-2
    path: '{moz}/try'
  - _created: \d+\.\d+ (re)
    _original_created: \d+\.\d+ (re)
    _original_partition: 7
    generaldelta: false
    name: hg-repo-init-2
    path: '{moz}/users/foo'

Cleanup

  $ hgmo clean
