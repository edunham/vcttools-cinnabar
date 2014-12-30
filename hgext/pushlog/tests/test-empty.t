  $ cat >> $HGRCPATH << EOF
  > [ui]
  > ssh = python "$TESTDIR/pylib/mercurial-support/dummyssh"
  > 
  > [extensions]
  > pushlog = $TESTDIR/hgext/pushlog
  > EOF

  $ export USER=hguser
  $ hg init server
  $ cd server
  $ hg serve -d -p $HGPORT --pid-file server.pid -E error.log -A access.log
  $ cat server.pid >> $DAEMON_PIDS
  $ cd ..

Cloning over SSH with no pushlog file should work

  $ ls server/.hg
  00changelog.i
  requires
  store

  $ hg clone ssh://user@dummy/$TESTTMP/server clone-ssh1
  no changes found
  added 0 pushes
  updating to branch default
  0 files updated, 0 files merged, 0 files removed, 0 files unresolved

This should have created an empty pushlog file

  $ ls server/.hg
  00changelog.i
  pushlog2.db
  requires
  store

  $ rm server/.hg/pushlog2.db

Cloning over HTTP with no pushlog file should work

  $ hg clone http://localhost:$HGPORT clone-http1
  no changes found
  added 0 pushes
  updating to branch default
  0 files updated, 0 files merged, 0 files removed, 0 files unresolved

This should have created an empty pushlog file

  $ ls server/.hg
  00changelog.i
  pushlog2.db
  requires
  store
