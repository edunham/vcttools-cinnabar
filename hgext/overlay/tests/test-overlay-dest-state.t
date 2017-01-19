  $ . $TESTDIR/hgext/overlay/tests/helpers.sh

  $ hg init source
  $ cd source
  $ echo 0 > foo
  $ hg -q commit -A -m 'add foo'
  $ echo 1 > bar
  $ hg -q commit -A -m 'add bar'
  $ hg cp foo foo-copy
  $ hg commit -m 'copy foo to foo-copy'
Chain copies so copyrev differs
  $ hg cp foo-copy foo-copy2
  $ hg commit -m 'copy foo-copy to foo-copy2'
  $ hg serve -d --pid-file hg.pid -p $HGPORT
  $ cat hg.pid >> $DAEMON_PIDS
  $ cd ..

  $ hg init dest
  $ cd dest
  $ echo root > root
  $ hg -q commit -A -m initial

First overlay works fine

  $ hg overlay http://localhost:$HGPORT --into subdir
  pulling http://localhost:$HGPORT into $TESTTMP/dest/.hg/localhost~3a* (glob)
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 4 changesets with 4 changes to 4 files
  bd685f66c1fc -> 81fcbcf78f0a: add foo
  13a44cc39ddc -> 0aac34b31cb4: add bar
  2bb8fd7676d0 -> 645c9fffdee6: copy foo to foo-copy
  0f7e081c425c -> 4930b59d9987: copy foo-copy to foo-copy2

Create a new changeset to import

  $ cd ../source
  $ echo 2 > baz
  $ hg -q commit -A -m 'add baz'
  $ cd ../dest

Addition of file in destination fails precondition testing

  $ hg -q up tip
  $ echo extra > subdir/extra-file
  $ hg -q commit -A -m 'add extra file'
  $ hg overlay http://localhost:$HGPORT --into subdir
  pulling http://localhost:$HGPORT into $TESTTMP/dest/.hg/localhost~3a* (glob)
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 2 changes to 2 files
  0f7e081c425c already processed as 4930b59d9987; skipping 4/5 revisions
  abort: files mismatch between source and destiation: extra-file
  (destination must match previously imported changeset (0f7e081c425c) exactly)
  [255]

  $ hg -q strip -r .

Removal of file in destination fails precondition testing

  $ hg rm subdir/bar
  $ hg commit -m 'remove bar'
  $ hg overlay http://localhost:$HGPORT --into subdir
  0f7e081c425c already processed as 4930b59d9987; skipping 4/5 revisions
  abort: files mismatch between source and destiation: bar
  (destination must match previously imported changeset (0f7e081c425c) exactly)
  [255]

  $ hg -q strip -r .

File mode difference in destination fails precondition testing

  $ chmod +x subdir/foo
  $ hg commit -m 'make foo executable'
  $ hg overlay http://localhost:$HGPORT --into subdir
  0f7e081c425c already processed as 4930b59d9987; skipping 4/5 revisions
  abort: file flags mismatch between source and destination for foo: (none) != x
  [255]

  $ hg -q strip -r .

File content difference in destination fails precondition testing

  $ echo rewritten > subdir/bar
  $ hg commit -m 'change bar'
  $ hg overlay http://localhost:$HGPORT --into subdir
  0f7e081c425c already processed as 4930b59d9987; skipping 4/5 revisions
  abort: content mismatch between source (0f7e081c425c) and destination (7874b1d840a6) in subdir/bar
  [255]

  $ hg -q strip -r .

No copy metadata in dest fails precondition testing

  $ hg rm subdir/foo-copy2
  $ hg commit -m 'remove foo-copy2'
  $ echo 0 > subdir/foo-copy2
  $ hg -q commit -A -m 'create foop-copy2 without copy metadata'
  $ hg overlay http://localhost:$HGPORT --into subdir
  0f7e081c425c already processed as 4930b59d9987; skipping 4/5 revisions
  abort: metadata mismatch for file subdir/foo-copy2 between source and dest: {'copy': 'foo-copy'} != None
  [255]

  $ hg -q strip -r .
  $ hg -q strip -r .

Metadata mismatch between source and dest fails precondition testing

  $ hg rm subdir/foo-copy2
  $ hg commit -m 'remove foo-copy2'
  $ hg cp root subdir/foo-copy2
  $ echo 0 > subdir/foo-copy2
  $ hg commit -m 'create foo-copy2 from different source'

  $ hg overlay http://localhost:$HGPORT --into subdir
  0f7e081c425c already processed as 4930b59d9987; skipping 4/5 revisions
  abort: metadata mismatch for file subdir/foo-copy2 between source and dest: {'copy': 'foo-copy'} != {'copy': 'root'}
  [255]
