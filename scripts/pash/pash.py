#! /usr/bin/python -u

from datetime import datetime
import logging
import os
import pwd
import sys

import hg_helper
import ldap_helper


def QuoteForPOSIX(string):
    '''quote a string so it can be used as an argument in a  posix shell

    According to: http://www.unix.org/single_unix_specification/
    2.2.1 Escape Character(Backslash)

    A backslash that is not quoted shall preserve the literal value
    of the following character, with the exception of a <newline>.

    2.2.2 Single-Quotes

    Enclosing characters in single-quotes( '' ) shall preserve
    the literal value of each character within the single-quotes.
    A single-quote cannot occur within single-quotes.

    from: http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/498202
    thank you google!
    '''
    return "\\'".join("'" + p + "'" for p in string.split("'"))


if __name__ == '__main__':
    os.environ['PYTHONPATH'] = '/repo/hg/libraries/'

    user = os.environ.get('USER')

    if user == 'root':
        root_shell = pwd.getpwuid(0)[6]
        ssh_command = os.environ.get('SSH_ORIGINAL_COMMAND')
        if ssh_command:
            os.system(root_shell + " -c " + QuoteForPOSIX(ssh_command))
        else:
            os.execl(root_shell, root_shell)
    else:
        server_port = os.environ.get('SSH_CONNECTION').split ()[-1]

        user_status = hg_helper.is_valid_user(user)
        if user_status == 2:
            sys.stderr.write('Your mercurial account has been disabled due \
                              to inactivity.\nPlease file a bug at \
                              https://bugzilla.mozilla.org (or \
                              http://tinyurl.com/2aveg9k) to re-activate \
                              your account.\n')
            sys.exit(0)

        elif user_status != 1:
            sys.stderr.write('You do not have a valid mercurial account!\n')
            sys.exit(0)

        # Run ldap access date toucher, silently fail and log if we're unable to write
        try:
           ldap_helper.update_ldap_attribute(user, 'hgAccessDate',
                                             datetime.utcnow().strftime("%Y%m%d%H%M%S.%fZ"),
                                             'ldap://ldap.db.scl3.mozilla.com',
                                             'ldap://ldapsync1.db.scl3.mozilla.com')
        except Exception:
           logging.basicConfig(filename='/var/log/pash.log', level=logging.DEBUG)
           logging.exception('Failed to update LDAP attributes for %s' % user)

        # hg.mozilla.org handler
        if server_port == "22":
            hg_helper.serve('hg.mozilla.org')

        # hg.ecmascript.org handler
        elif server_port == "222":
            hg_helper.serve('hg.ecmascript.org')

        sys.exit (0)
