configurebzexport() {
  USERNAME=$3
  if [ -z $USERNAME ]; then
    USERNAME=admin@example.com
  fi
  PASSWORD=$4
  if [ -z $PASSWORD ]; then
    PASSWORD=password
  fi

  export BUGZILLA_URL=http://${DOCKER_HOSTNAME}:$1

  cat >> $2 << EOF
[extensions]
mq =
bzexport = $TESTDIR/hgext/bzexport

[bzexport]
api_server = ${BUGZILLA_URL}/bzapi/
bugzilla = ${BUGZILLA_URL}/
username = ${USERNAME}
password = ${PASSWORD}
EOF
}

alias bugzilla=$TESTDIR/bugzilla
