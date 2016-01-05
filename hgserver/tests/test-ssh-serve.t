#require hgmodocker

  $ . $TESTDIR/hgserver/tests/helpers.sh
  $ hgmoenv

  $ hgmo create-ldap-user user@example.com testuser 1000 'Test User' --key-file testuser --scm-level 1

  $ cat >> $HGRCPATH << EOF
  > [ui]
  > ssh = ssh -F `pwd`/ssh_config -i `pwd`/testuser -l user@example.com
  > EOF

We are able to clone via SSH

  $ hgmo create-repo repo1 1
  (recorded repository creation in replication log)
  $ hg clone ssh://$SSH_SERVER:$HGPORT/repo1
  destination directory: repo1
  no changes found
  updating to branch default
  0 files updated, 0 files merged, 0 files removed, 0 files unresolved

Invalid paths are rejected

  $ hg clone ssh://$SSH_SERVER:$HGPORT/foo/../../etc/password
  remote: Only alpha-numeric characters, ".", and "-" are allowed in the repository names.
  remote: Please try again with only those characters.
  abort: no suitable response from remote hg!
  [255]

A push works

  $ cd repo1
  $ touch foo
  $ hg -q commit -A -m initial
  $ hg push ssh://$SSH_SERVER:$HGPORT/repo1
  pushing to ssh://*:$HGPORT/repo1 (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: recorded push in pushlog
  remote: 
  remote: View your change here:
  remote:   https://hg.mozilla.org/repo1/rev/77538e1ce4be
  remote: recorded changegroup in replication log in \d\.\d+s (re)

  $ hgmo clean
