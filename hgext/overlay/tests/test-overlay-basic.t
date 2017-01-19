  $ . $TESTDIR/hgext/overlay/tests/helpers.sh

  $ hg init source
  $ cd source
  $ mkdir dir0
  $ echo dir0/file0 > dir0/file0
  $ echo dir0/file1 > dir0/file1
  $ hg -q commit -A -m 'add dir0/file0 and dir0/file1'
  $ mkdir dir1
  $ echo dir1/file0 > dir1/file0
  $ hg -q commit -A -m 'add dir1/file0'
  $ hg serve -d --pid-file hg.pid -p $HGPORT
  $ cat hg.pid >> $DAEMON_PIDS

  $ cd ..

  $ hg init dest
  $ cd dest
  $ echo foo > foo
  $ hg -q commit -A -m initial
  $ hg -q up null

  $ hg overlay http://localhost:$HGPORT --into subdir
  pulling http://localhost:$HGPORT into $TESTTMP/dest/.hg/localhost~3a* (glob)
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 2 changesets with 3 changes to 3 files
  44791c369f4c -> c129882b47be: add dir0/file0 and dir0/file1
  afdf9d98d53c -> 3c931698b680: add dir1/file0

  $ hg log -p --debug
  changeset:   2:3c931698b680b225f15c9a27fc0aee486afc11cb
  tag:         tip
  phase:       draft
  parent:      1:c129882b47be0de27c3e32ab643aa36d193ccea7
  parent:      -1:0000000000000000000000000000000000000000
  manifest:    2:e6f7ff8522567635c175d22989b27eef6cb8df60
  user:        Test User <someone@example.com>
  date:        Thu Jan 01 00:00:00 1970 +0000
  files+:      subdir/dir1/file0
  extra:       branch=default
  extra:       subtree_revision=afdf9d98d53cb160d4a61267450bf32a8d1aa534
  extra:       subtree_source=https://example.com/repo
  description:
  add dir1/file0
  
  
  diff --git a/subdir/dir1/file0 b/subdir/dir1/file0
  new file mode 100644
  --- /dev/null
  +++ b/subdir/dir1/file0
  @@ -0,0 +1,1 @@
  +dir1/file0
  
  changeset:   1:c129882b47be0de27c3e32ab643aa36d193ccea7
  phase:       draft
  parent:      0:21e2edf037c2267b7c1d7a038d64bca58d5caa59
  parent:      -1:0000000000000000000000000000000000000000
  manifest:    1:29cd433f4d664a8e65770ff64d91830206f30e9c
  user:        Test User <someone@example.com>
  date:        Thu Jan 01 00:00:00 1970 +0000
  files+:      subdir/dir0/file0 subdir/dir0/file1
  extra:       branch=default
  extra:       subtree_revision=44791c369f4cd3098f627ec7ef4a014946f5a5ae
  extra:       subtree_source=https://example.com/repo
  description:
  add dir0/file0 and dir0/file1
  
  
  diff --git a/subdir/dir0/file0 b/subdir/dir0/file0
  new file mode 100644
  --- /dev/null
  +++ b/subdir/dir0/file0
  @@ -0,0 +1,1 @@
  +dir0/file0
  diff --git a/subdir/dir0/file1 b/subdir/dir0/file1
  new file mode 100644
  --- /dev/null
  +++ b/subdir/dir0/file1
  @@ -0,0 +1,1 @@
  +dir0/file1
  
  changeset:   0:21e2edf037c2267b7c1d7a038d64bca58d5caa59
  phase:       draft
  parent:      -1:0000000000000000000000000000000000000000
  parent:      -1:0000000000000000000000000000000000000000
  manifest:    0:9091aa5df980aea60860a2e39c95182e68d1ddec
  user:        Test User <someone@example.com>
  date:        Thu Jan 01 00:00:00 1970 +0000
  files+:      foo
  extra:       branch=default
  description:
  initial
  
  
  diff --git a/foo b/foo
  new file mode 100644
  --- /dev/null
  +++ b/foo
  @@ -0,0 +1,1 @@
  +foo
  
  $ hg files -r tip
  foo
  subdir/dir0/file0
  subdir/dir0/file1
  subdir/dir1/file0
