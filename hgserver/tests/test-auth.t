#require hgmodocker

  $ . $TESTDIR/hgserver/tests/helpers.sh
  $ hgmoenv

Attempting to SSH into pash as an unknown user is denied

  $ ssh-keygen -b 2048 -t rsa -f key1 -N '' > /dev/null

  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER
  Permission denied (publickey).\r (esc)
  [255]

SSH as a valid user without proper key

  $ hgmo create-ldap-user user1@example.com user1 1000 'Some User'
  $ hgmo exec hgssh /usr/bin/ldapsearch -b 'dc=mozilla' -s sub -x mail=user1@example.com
  # extended LDIF
  #
  # LDAPv3
  # base <dc=mozilla> with scope subtree
  # filter: mail=user1@example.com
  # requesting: ALL
  #
  
  # user1@example.com, com, mozilla
  dn: mail=user1@example.com,o=com,dc=mozilla
  objectClass: inetOrgPerson
  objectClass: organizationalPerson
  objectClass: person
  objectClass: posixAccount
  objectClass: top
  objectClass: hgAccount
  cn: Some User
  gidNumber: 100
  homeDirectory: /home/user1
  sn: User
  uid: user1
  uidNumber: 1000
  fakeHome: /tmp
  hgAccountEnabled: TRUE
  hgHome: /tmp
  hgShell: /bin/sh
  mail: user1@example.com
  
  # search result
  search: 2
  result: 0 Success
  
  # numResponses: 2
  # numEntries: 1

  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER
  Permission denied (publickey).\r (esc)
  [255]

SSH with a valid key gives us warning about no command

  $ hgmo add-ssh-key user1@example.com - < key1.pub
  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER
  A SSH connection has been successfully established.
  
  Your account (user1@example.com) has privileges to access Mercurial over
  SSH.
  
  You did not specify a command to run on the server. This server only
  supports running specific commands. Since there is nothing to do, you
  are being disconnected.
  [1]

SSH with invalid command prints appropriate error message

  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER foobar
  A SSH connection has been successfully established.
  
  Your account (user1@example.com) has privileges to access Mercurial over
  SSH.
  
  The command you specified is not allowed on this server.
  
  Goodbye.
  [1]

Successful login should set hgAccessDate LDAP attribute

  $ hgmo exec hgssh /usr/bin/ldapsearch -b 'dc=mozilla' -s sub -x mail=user1@example.com
  # extended LDIF
  #
  # LDAPv3
  # base <dc=mozilla> with scope subtree
  # filter: mail=user1@example.com
  # requesting: ALL
  #
  
  # user1@example.com, com, mozilla
  dn: mail=user1@example.com,o=com,dc=mozilla
  objectClass: inetOrgPerson
  objectClass: organizationalPerson
  objectClass: person
  objectClass: posixAccount
  objectClass: top
  objectClass: hgAccount
  objectClass: ldapPublicKey
  cn: Some User
  gidNumber: 100
  homeDirectory: /home/user1
  sn: User
  uid: user1
  uidNumber: 1000
  fakeHome: /tmp
  hgAccountEnabled: TRUE
  hgHome: /tmp
  hgShell: /bin/sh
  mail: user1@example.com
  sshPublicKey: ssh-rsa * (glob)
   * (glob)
   * (glob)
   * (glob)
   * (glob)
   * (glob)
  hgAccessDate: 2\d{3}\d{2}\d{2}\d{2}\d{2}\d{2}\.\d+Z (re)
  
  # search result
  search: 2
  result: 0 Success
  
  # numResponses: 2
  # numEntries: 1

No HG access prints helpful error message

  $ hgmo create-ldap-user --no-hg-access --key-file key1 nohgaccess@example.com nohgaccess 1001 'No HgAccess'
  $ hgmo exec hgssh /usr/bin/ldapsearch -b 'dc=mozilla' -s sub -x mail=nohgaccess@example.com
  # extended LDIF
  #
  # LDAPv3
  # base <dc=mozilla> with scope subtree
  # filter: mail=nohgaccess@example.com
  # requesting: ALL
  #
  
  # nohgaccess@example.com, com, mozilla
  dn: mail=nohgaccess@example.com,o=com,dc=mozilla
  objectClass: inetOrgPerson
  objectClass: organizationalPerson
  objectClass: person
  objectClass: posixAccount
  objectClass: top
  objectClass: ldapPublicKey
  cn: No HgAccess
  gidNumber: 100
  homeDirectory: /home/nohgaccess
  sn: HgAccess
  uid: nohgaccess
  uidNumber: 1001
  mail: nohgaccess@example.com
  sshPublicKey:* (glob)
   * (glob)
   * (glob)
   * (glob)
   * (glob)
   * (glob)
   * (glob)
   * (glob)
  
  # search result
  search: 2
  result: 0 Success
  
  # numResponses: 2
  # numEntries: 1

  $ ssh -T -F ssh_config -i key1 -l nohgaccess@example.com -p $HGPORT $SSH_SERVER
  Could not chdir to home directory : No such file or directory
  A SSH connection has been established and your account (nohgaccess@example.com)
  was found in LDAP.
  
  However, Mercurial access is not currently enabled on your LDAP account.
  
  Please follow the instructions at the following URL to gain Mercurial
  access:
  
      https://www.mozilla.org/en-US/about/governance/policies/commit/

Do another login to verify no pash errors are present

  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER
  A SSH connection has been successfully established.
  
  Your account (user1@example.com) has privileges to access Mercurial over
  SSH.
  
  You did not specify a command to run on the server. This server only
  supports running specific commands. Since there is nothing to do, you
  are being disconnected.
  [1]

  $ hgmo exec hgssh cat /var/log/pash.log

Failure to connect to LDAP mirror is fatal

  $ hgmo exec hgssh /set-ldap-property url ldap://localhost:6000
  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER
  Could not connect to the LDAP server at ldap://localhost:6000
  [1]

  $ hgmo exec hgssh /set-ldap-property url real

Failure to connect to LDAP master server is not fatal

  $ hgmo exec hgssh /set-ldap-property write_url ldap://localhost:6000

  $ ssh -T -F ssh_config -i key1 -l user1@example.com -p $HGPORT $SSH_SERVER
  Could not connect to the LDAP server at ldap://localhost:6000
  A SSH connection has been successfully established.
  
  Your account (user1@example.com) has privileges to access Mercurial over
  SSH.
  
  You did not specify a command to run on the server. This server only
  supports running specific commands. Since there is nothing to do, you
  are being disconnected.
  [1]

Can pull when LDAP master is not available

  $ hgmo create-repo mozilla-central 3
  (recorded repository creation in replication log)
  $ cat >> $HGRCPATH << EOF
  > [ui]
  > ssh = ssh -F `pwd`/ssh_config -i `pwd`/key1 -l user1@example.com
  > EOF

  $ hg clone ssh://${SSH_SERVER}:${HGPORT}/mozilla-central
  remote: Could not connect to the LDAP server at ldap://localhost:6000
  destination directory: mozilla-central
  no changes found
  updating to branch default
  0 files updated, 0 files merged, 0 files removed, 0 files unresolved

  $ hgmo exec hgssh /set-ldap-property write_url real

Cleanup

  $ hgmo clean
