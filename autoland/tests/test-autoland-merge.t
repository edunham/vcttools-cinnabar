#require mozreviewdocker

  $ . $TESTDIR/hgext/reviewboard/tests/helpers.sh
  $ commonenv
  $ mozreview create-user cthulhu@example.com password 'Cthulhu :cthulhu'
  Created user 6

Create an initial revision.

  $ cd client
  $ echo foo > foo
  $ hg commit -A -m 'root commit'
  adding foo
  $ hg phase --public -r .

Create a commit to test on Try

  $ bugzilla create-bug TestProduct TestComponent 'First Bug'
  $ echo initial > foo
  $ hg commit -m 'Bug 1 - some stuff; r?cthulhu'
  $ hg push
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT6/test-repo
  (adding commit id to 1 changesets)
  saved backup bundle to $TESTTMP/client/.hg/strip-backup/633b0929fc18-25aef645-addcommitid.hg (glob)
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 2 changesets with 2 changes to 1 files
  remote: recorded push in pushlog
  submitting 1 changesets for review
  
  changeset:  1:b92ab6726259
  summary:    Bug 1 - some stuff; r?cthulhu
  review:     http://$DOCKER_HOSTNAME:$HGPORT1/r/2 (draft)
  
  review id:  bz://1/mynick
  review url: http://$DOCKER_HOSTNAME:$HGPORT1/r/1 (draft)
  (visit review url to publish these review requests so others can see them)

Post a job

  $ REV=`hg log -r . --template "{node|short}"`
  $ ottoland post-autoland-job $AUTOLAND_URL test-repo $REV inbound http://localhost:9898 --commit-descriptions "{\"$REV\": \"Bug 1 - some stuff; r=cthulhu\"}"
  (200, u'{\n  "request_id": 1\n}')
  $ ottoland autoland-job-status $AUTOLAND_URL 1 --poll
  (200, u'{\n  "commit_descriptions": {\n    "b92ab6726259": "Bug 1 - some stuff; r=cthulhu"\n  }, \n  "destination": "inbound", \n  "error_msg": "", \n  "landed": true, \n  "ldap_username": "autolanduser@example.com", \n  "push_bookmark": "", \n  "result": "9dc773c72939", \n  "rev": "b92ab6726259", \n  "tree": "test-repo", \n  "trysyntax": ""\n}')
  $ mozreview exec autoland hg log /repos/inbound-test-repo/ --template '{rev}:{desc\|firstline}:{phase}\\n'
  0:Bug 1 - some stuff; r=cthulhu:public

Post a job with a bad merge

  $ mozreview exec autoland "bash -c cd /repos/inbound-test-repo/ && echo foo2 > foo"
  $ mozreview exec autoland "bash -c cd /repos/inbound-test-repo/ && hg commit -m \"trouble\""
  $ echo foo3 > foo
  $ hg commit -m 'Bug 1 - more stuff; r?cthulhu'
  $ hg push
  pushing to ssh://$DOCKER_HOSTNAME:$HGPORT6/test-repo
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 2 changesets with 2 changes to 1 files
  remote: recorded push in pushlog
  submitting 3 changesets for review
  
  changeset:  1:b92ab6726259
  summary:    Bug 1 - some stuff; r?cthulhu
  review:     http://$DOCKER_HOSTNAME:$HGPORT1/r/2 (draft)
  
  changeset:  2:c698e9b61b34
  summary:    trouble
  review:     http://$DOCKER_HOSTNAME:$HGPORT1/r/3 (draft)
  
  changeset:  3:b28c57a24a9e
  summary:    Bug 1 - more stuff; r?cthulhu
  review:     http://$DOCKER_HOSTNAME:$HGPORT1/r/4 (draft)
  
  review id:  bz://1/mynick
  review url: http://$DOCKER_HOSTNAME:$HGPORT1/r/1 (draft)
  (review requests lack reviewers; visit review url to assign reviewers)
  (visit review url to publish these review requests so others can see them)
  $ REV=`hg log -r . --template "{node|short}"`
  $ ottoland post-autoland-job $AUTOLAND_URL test-repo $REV inbound http://localhost:9898 --commit-descriptions "{\"$REV\": \"Bug 1 - more stuff; r=cthulhu\"}"
  (200, u'{\n  "request_id": 2\n}')
  $ ottoland autoland-job-status $AUTOLAND_URL 2 --poll
  (200, u'{\n  "commit_descriptions": {\n    "b28c57a24a9e": "Bug 1 - more stuff; r=cthulhu"\n  }, \n  "destination": "inbound", \n  "error_msg": "We\'re sorry, Autoland could not rebase your commits for you automatically. Please manually rebase your commits and try again.\\n\\nhg error in cmd: hg rebase -s 8a6b70a9c3b6 -d 9dc773c72939: rebasing 4:8a6b70a9c3b6 \\"Bug 1 - more stuff; r=cthulhu\\" (tip)\\nmerging foo\\nwarning: conflicts while merging foo! (edit, then use \'hg resolve --mark\')\\nunresolved conflicts (see hg resolve, then hg rebase --continue)\\n", \n  "landed": false, \n  "ldap_username": "autolanduser@example.com", \n  "push_bookmark": "", \n  "result": "", \n  "rev": "b28c57a24a9e", \n  "tree": "test-repo", \n  "trysyntax": ""\n}')

  $ mozreview exec autoland hg log /repos/inbound-test-repo/ --template '{rev}:{desc\|firstline}:{phase}\\n'
  0:Bug 1 - some stuff; r=cthulhu:public

  $ mozreview stop
  stopped 10 containers
