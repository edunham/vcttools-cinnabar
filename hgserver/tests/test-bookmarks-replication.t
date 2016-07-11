#require hgmodocker

  $ . $TESTDIR/hgserver/tests/helpers.sh
  $ hgmoenv
  $ standarduser

  $ hgmo create-repo mozilla-central scm_level_1
  (recorded repository creation in replication log)
  $ hg -q clone ssh://$SSH_SERVER:$HGPORT/mozilla-central
  $ cd mozilla-central

  $ touch foo
  $ hg -q commit -A -m initial
  $ hg bookmark bm1
  $ echo bm1 > foo
  $ hg commit -m 'bm1 commit 1'
  $ hg -q up -r 0
  $ hg bookmark bm2
  $ echo bm2 > foo
  $ hg commit -m 'bm2 commit 1'
  created new head

  $ hg log -G
  @  changeset:   2:e7d8e0aefcf6
  |  bookmark:    bm2
  |  tag:         tip
  |  parent:      0:77538e1ce4be
  |  user:        Test User <someone@example.com>
  |  date:        Thu Jan 01 00:00:00 1970 +0000
  |  summary:     bm2 commit 1
  |
  | o  changeset:   1:04da6c25817b
  |/   bookmark:    bm1
  |    user:        Test User <someone@example.com>
  |    date:        Thu Jan 01 00:00:00 1970 +0000
  |    summary:     bm1 commit 1
  |
  o  changeset:   0:77538e1ce4be
     user:        Test User <someone@example.com>
     date:        Thu Jan 01 00:00:00 1970 +0000
     summary:     initial
  

  $ hg push -B bm1
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 2 changesets with 2 changes to 1 files
  remote: recorded push in pushlog
  remote: 
  remote: View your changes here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/77538e1ce4be
  remote:   https://hg.mozilla.org/mozilla-central/rev/04da6c25817b
  remote: recorded changegroup in replication log in \d+\.\d+s (re)
  exporting bookmark bm1

  $ hg push -B bm2
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files (+1 heads)
  remote: recorded push in pushlog
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/e7d8e0aefcf6
  remote: recorded changegroup in replication log in \d\.\d+s (re)
  exporting bookmark bm2

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

Bookmarks get replicated to mirrors

  $ http --no-headers ${HGWEB_0_URL}mozilla-central/json-bookmarks
  200
  
  {
  "node": "e7d8e0aefcf6bcc137a21978e9a431c5b0dafd86",
  "bookmarks": [{
  "bookmark": "bm1",
  "node": "04da6c25817b564b37238ee5144e5adf2af0cb5b",
  "date": [0.0, 0]
  }, {
  "bookmark": "bm2",
  "node": "e7d8e0aefcf6bcc137a21978e9a431c5b0dafd86",
  "date": [0.0, 0]
  }]
  }

Push a bookmark update

  $ echo bm2_2 > foo
  $ hg commit -m 'bm2 commit 2'
  $ hg log -r tip
  changeset:   3:b222465a31a1
  bookmark:    bm2
  tag:         tip
  user:        Test User <someone@example.com>
  date:        Thu Jan 01 00:00:00 1970 +0000
  summary:     bm2 commit 2
  

  $ hg push -B bm2
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: recorded push in pushlog
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/mozilla-central/rev/b222465a31a1
  remote: recorded changegroup in replication log in \d\.\d+s (re)
  updating bookmark bm2

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ http --no-headers ${HGWEB_0_URL}mozilla-central/json-bookmarks
  200
  
  {
  "node": "b222465a31a101470b94a950392809801a91d3da",
  "bookmarks": [{
  "bookmark": "bm1",
  "node": "04da6c25817b564b37238ee5144e5adf2af0cb5b",
  "date": [0.0, 0]
  }, {
  "bookmark": "bm2",
  "node": "b222465a31a101470b94a950392809801a91d3da",
  "date": [0.0, 0]
  }]
  }

Push a non-forward bookmark update

  $ hg up -r 1
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  (leaving bookmark bm2)
  $ hg bookmark -f bm2
  $ hg push -B bm2
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  no changes found
  remote: recorded updates to bookmarks in replication log in \d\.\d+s (re)
  updating bookmark bm2
  [1]

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ http --no-headers ${HGWEB_0_URL}mozilla-central/json-bookmarks
  200
  
  {
  "node": "b222465a31a101470b94a950392809801a91d3da",
  "bookmarks": [{
  "bookmark": "bm1",
  "node": "04da6c25817b564b37238ee5144e5adf2af0cb5b",
  "date": [0.0, 0]
  }, {
  "bookmark": "bm2",
  "node": "04da6c25817b564b37238ee5144e5adf2af0cb5b",
  "date": [0.0, 0]
  }]
  }

Push a bookmark delete

  $ hg bookmark -d bm1
  $ hg push -B bm1
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  no changes found
  remote: recorded updates to bookmarks in replication log in \d\.\d+s (re)
  deleting remote bookmark bm1
  [1]

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ http --no-headers ${HGWEB_0_URL}mozilla-central/json-bookmarks
  200
  
  {
  "node": "b222465a31a101470b94a950392809801a91d3da",
  "bookmarks": [{
  "bookmark": "bm2",
  "node": "04da6c25817b564b37238ee5144e5adf2af0cb5b",
  "date": [0.0, 0]
  }]
  }

Remove all bookmarks

  $ hg bookmark -d bm2
  $ hg push -B bm2
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT/mozilla-central
  searching for changes
  no changes found
  remote: recorded updates to bookmarks in replication log in \d\.\d+s (re)
  deleting remote bookmark bm2
  [1]

  $ hgmo exec hgweb0 /var/hg/venv_replication/bin/vcsreplicator-consumer --wait-for-no-lag /etc/mercurial/vcsreplicator.ini

  $ http --no-headers ${HGWEB_0_URL}mozilla-central/json-bookmarks
  200
  
  {
  "node": "b222465a31a101470b94a950392809801a91d3da",
  "bookmarks": []
  }

Cleanup

  $ cd ..
  $ hgmo clean
