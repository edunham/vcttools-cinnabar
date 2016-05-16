  $ . $TESTDIR/hgext/robustcheckout/tests/helpers.sh

Missing parent of destination directory will be created automatically

  $ hg robustcheckout http://localhost:$HGPORT/repo0 parent0/parent1/dest --revision 5d6cdc75a09b
  ensuring http://localhost:$HGPORT/repo0@5d6cdc75a09b is available at parent0/parent1/dest
  (sharing from new pooled repository b8b78f0253d822e33ba652fd3d80a5c0837cfdf3)
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 3 changesets with 3 changes to 1 files (+1 heads)
  searching for changes
  no changes found
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  updated to 5d6cdc75a09bcccf76f9339a28e1d89360c59dce

Missing parent of share pool directory will be created automatically

  $ hg robustcheckout http://localhost:$HGPORT/repo0 dest --revision 5d6cdc75a09b --sharebase shareparent/sharebase
  ensuring http://localhost:$HGPORT/repo0@5d6cdc75a09b is available at dest
  (sharing from new pooled repository b8b78f0253d822e33ba652fd3d80a5c0837cfdf3)
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 3 changesets with 3 changes to 1 files (+1 heads)
  searching for changes
  no changes found
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  updated to 5d6cdc75a09bcccf76f9339a28e1d89360c59dce
