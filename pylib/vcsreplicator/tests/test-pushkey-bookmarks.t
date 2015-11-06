#require hgmodocker

  $ . $TESTDIR/pylib/vcsreplicator/tests/helpers.sh
  $ vcsrenv
  $ standarduser

Create the repository and push a change

  $ hgmo create-repo mozilla-central 1
  (recorded repository creation in replication log)
  $ consumer --onetime
  $ consumer --onetime
  WARNING:vcsreplicator.consumer:created Mercurial repository: $TESTTMP/repos/mozilla-central

  $ hg -q clone ssh://${SSH_SERVER}:${SSH_PORT}/mozilla-central
  $ cd mozilla-central
  $ touch foo
  $ hg -q commit -A -m initial
  $ hg push
  pushing to ssh://*:$HGPORT/mozilla-central (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: recorded push in pushlog
  remote: legacy replication of phases disabled because vcsreplicator is loaded
  remote: legacy replication of changegroup disabled because vcsreplicator is loaded
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/77538e1ce4be
  remote: recorded changegroup in replication log in \d\.\d+s (re)

Phases should be updated on normal push

  $ consumer --dump
  - name: heartbeat-1
  - name: heartbeat-1
  - heads:
    - 77538e1ce4bec5f7aac58a7ceca2da0e38e90a72
    name: hg-changegroup-1
    nodes:
    - 77538e1ce4bec5f7aac58a7ceca2da0e38e90a72
    path: '{moz}/mozilla-central'
    source: serve

  $ consumer --onetime
  $ consumer --onetime
  $ consumer --onetime
  WARNING:vcsreplicator.consumer:pulling 1 heads from ssh://*:$HGPORT/mozilla-central into $TESTTMP/repos/mozilla-central (glob)
  WARNING:vcsreplicator.consumer:pulled 1 changesets into $TESTTMP/repos/mozilla-central

Creating a bookmark will write a pushkey message

  $ hg bookmark my-bookmark
  $ hg push -B my-bookmark
  pushing to ssh://*:$HGPORT/mozilla-central (glob)
  searching for changes
  no changes found
  remote: legacy replication of bookmarks disabled because vcsreplicator is loaded
  remote: recorded updates to bookmarks in replication log in \d\.\d+s (re)
  exporting bookmark my-bookmark
  [1]

  $ consumer --dump
  - name: heartbeat-1
  - name: heartbeat-1
  - key: my-bookmark
    name: hg-pushkey-1
    namespace: bookmarks
    new: 77538e1ce4bec5f7aac58a7ceca2da0e38e90a72
    old: ''
    path: '{moz}/mozilla-central'
    ret: true

  $ hg -R $TESTTMP/repos/mozilla-central bookmarks
  no bookmarks set

Consuming the pushkey message will create a bookmark

  $ consumer --onetime
  $ consumer --onetime
  $ consumer --onetime

  $ hg -R $TESTTMP/repos/mozilla-central bookmarks
     my-bookmark               0:77538e1ce4be

Simulate a client that is behind processing
We send a changegroup and a pushkey but don't process them immediately

  $ echo laggy-mirror > foo
  $ hg commit -m 'simulate laggy mirror'
  $ hg push
  pushing to ssh://*:$HGPORT/mozilla-central (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: recorded push in pushlog
  remote: legacy replication of phases disabled because vcsreplicator is loaded
  remote: legacy replication of bookmarks disabled because vcsreplicator is loaded
  remote: legacy replication of changegroup disabled because vcsreplicator is loaded
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/2777163b5938
  remote: recorded changegroup in replication log in \d\.\d+s (re)
  updating bookmark my-bookmark

  $ consumer --dump
  - name: heartbeat-1
  - name: heartbeat-1
  - heads:
    - 2777163b593873bfa63c7129e02a21becc299ff0
    name: hg-changegroup-1
    nodes:
    - 2777163b593873bfa63c7129e02a21becc299ff0
    path: '{moz}/mozilla-central'
    source: serve

Mirror gets bookmark updates when pulling the changegroup.

  $ consumer --onetime
  $ consumer --onetime
  $ consumer --onetime
  WARNING:vcsreplicator.consumer:pulling 1 heads from ssh://*:$HGPORT/mozilla-central into $TESTTMP/repos/mozilla-central (glob)
  WARNING:vcsreplicator.consumer:pulled 1 changesets into $TESTTMP/repos/mozilla-central

  $ hg -R $TESTTMP/repos/mozilla-central bookmarks
     my-bookmark               1:2777163b5938

Now try something more advanced. Let's do 2 pushes to the server and
see what happens when the mirror pulls a non-tip that no longer has a
bookmark attached.

  $ echo double-laggy-1 > foo
  $ hg commit -m 'double laggy 1'
  $ hg -q push
  $ echo double-laggy-2 > foo
  $ hg commit -m 'double laggy 2'
  $ hg -q push

We should have 2 changegroup messages

  $ consumer --dump
  - name: heartbeat-1
  - name: heartbeat-1
  - heads:
    - 031adcaa8ee7e23dd05ce5900645e771a3637682
    name: hg-changegroup-1
    nodes:
    - 031adcaa8ee7e23dd05ce5900645e771a3637682
    path: '{moz}/mozilla-central'
    source: serve
  - name: heartbeat-1
  - name: heartbeat-1
  - heads:
    - e20ecd72ffa991598a1b26333788345377318231
    name: hg-changegroup-1
    nodes:
    - e20ecd72ffa991598a1b26333788345377318231
    path: '{moz}/mozilla-central'
    source: serve

If the mirror pulls, it will see the bookmark attached to a changeset
it doesn't know about since it hasn't pulled it yet. It shouldn't touch
the bookmark.

  $ consumer --onetime
  $ consumer --onetime

  $ hg -R $TESTTMP/repos/mozilla-central bookmarks
     my-bookmark               1:2777163b5938

  $ consumer --onetime
  WARNING:vcsreplicator.consumer:pulling 1 heads from ssh://*:$HGPORT/mozilla-central into $TESTTMP/repos/mozilla-central (glob)
  WARNING:vcsreplicator.consumer:pulled 1 changesets into $TESTTMP/repos/mozilla-central

  $ hg -R $TESTTMP/repos/mozilla-central bookmarks
     my-bookmark               1:2777163b5938

But processing the next changegroup message should advance the bookmark by 1

  $ consumer --onetime
  $ consumer --onetime
  $ consumer --onetime
  WARNING:vcsreplicator.consumer:pulling 1 heads from ssh://*:$HGPORT/mozilla-central into $TESTTMP/repos/mozilla-central (glob)
  WARNING:vcsreplicator.consumer:pulled 1 changesets into $TESTTMP/repos/mozilla-central

  $ hg -R $TESTTMP/repos/mozilla-central bookmarks
     my-bookmark               3:e20ecd72ffa9

Cleanup

  $ hgmo stop
