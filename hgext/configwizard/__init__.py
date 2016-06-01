# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Manage Mercurial configuration in a Mozilla-tailored way."""

import difflib
import io
import os
import subprocess
import sys
import uuid

from mercurial import (
    cmdutil,
    error,
    scmutil,
    util,
)
from mercurial.i18n import _

OUR_DIR = os.path.dirname(__file__)
execfile(os.path.join(OUR_DIR, '..', 'bootstrap.py'))

from configobj import ConfigObj

INITIAL_MESSAGE = '''
This wizard will guide you through configuring Mercurial for an optimal
experience contributing to Mozilla projects.

The wizard makes no changes without your permission.

To begin, press the enter/return key.
'''.lstrip()

MINIMUM_SUPPORTED_VERSION = (3, 5, 0)

OLDEST_NON_LEGACY_VERSION = (3, 7, 3)

VERSION_TOO_OLD = '''
Your version of Mercurial is too old to run `hg configwizard`.

Mozilla's Mercurial support policy is to support at most the past
1 year of Mercurial releases (or 4 major Mercurial releases).
'''.lstrip()

LEGACY_MERCURIAL_MESSAGE = '''
You are running an out of date Mercurial client (%s).

For a faster and better Mercurial experience, we HIGHLY recommend you
upgrade.

Legacy versions of Mercurial have known security vulnerabilities. Failure
to upgrade may leave you exposed. You are highly encouraged to upgrade in
case you aren't running a patched version.
'''.lstrip()

MISSING_USERNAME = '''
You don't have a username defined in your Mercurial config file. In order
to author commits, you'll need to define a name and e-mail address.

This data will be publicly available when you send commits/patches to others.
If you aren't comfortable giving us your full name, pseudonames are
acceptable.

(Relevant config option: ui.username)
'''.lstrip()

BAD_DIFF_SETTINGS = '''
Mercurial is not configured to produce diffs in a more readable format.

Would you like to change this (Yn)? $$ &Yes $$ &No
'''.strip()

FSMONITOR_INFO = '''
The fsmonitor extension integrates the watchman filesystem watching tool
with Mercurial. Commands like `hg status`, `hg diff`, and `hg commit`
(which need to examine filesystem state) can query watchman to obtain
this state, allowing these commands to complete much quicker.

When installed, the fsmonitor extension will automatically launch a
background watchman daemon for accessed Mercurial repositories. It
should "just work."

Would you like to enable fsmonitor (Yn)? $$ &Yes $$ &No
'''.strip()

FSMONITOR_NOT_AVAILABLE = '''
Newer versions of Mercurial have built-in support for integrating with
filesystem watching services to make common operations faster.

This integration is STRONGLY RECOMMENDED when using the Firefox
repository.

Please upgrade to Mercurial 3.8+ so this feature is available.
'''.lstrip()

FIREFOXTREE_INFO = '''
The firefoxtree extension makes interacting with the multiple Firefox
repositories easier:

* Aliases for common trees are pre-defined. e.g. `hg pull central`
* Pulling from known Firefox trees will create "remote refs" appearing as
  tags. e.g. pulling from fx-team will produce a "fx-team" tag.
* The `hg fxheads` command will list the heads of all pulled Firefox repos
  for easy reference.
* `hg push` will limit itself to pushing a single head when pushing to
  Firefox repos.
* A pre-push hook will prevent you from pushing multiple heads to known
  Firefox repos. This acts quicker than a server-side hook.

The firefoxtree extension is *strongly* recommended if you:

a) aggregate multiple Firefox repositories into a single local repo
b) perform head/bookmark-based development (as opposed to mq)

(Relevant config option: extensions.firefoxtree)

Would you like to activate firefoxtree (Yn)? $$ &Yes $$ &No
'''.strip()

CODEREVIEW_INFO = '''
Commits to Mozilla projects are typically sent to MozReview. This is the
preferred code review tool at Mozilla.

Some still practice a legacy code review workflow that uploads patches
to Bugzilla.

1. MozReview only (preferred)
2. Both MozReview and Bugzilla
3. Bugzilla only

Which code review tools will you be submitting code to? $$ &1 $$ &2 $$ &3
'''.strip()

MISSING_BUGZILLA_CREDENTIALS = '''
You do not have a Bugzilla API Key defined in your Mercurial config.

In order to communicate with Bugzilla and services (like MozReview) that
use Bugzilla for authentication, you'll need to supply an API Key.

The Bugzilla API Key is optional. However, if you don't supply one,
certain features may not work and/or you'll be prompted for one.

You should only need to configure a Bugzilla API Key once.
'''.lstrip()

BUGZILLA_API_KEY_INSTRUCTIONS = '''
Bugzilla API Keys can only be obtained through the Bugzilla web interface.

Please perform the following steps:

  1) Open https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey
  2) Generate a new API Key
  3) Copy the generated key and paste it here
'''.lstrip()

LEGACY_BUGZILLA_CREDENTIALS_DETECTED = '''
Your existing Mercurial config uses a legacy method for defining Bugzilla
credentials. Bugzilla API Keys are the most secure and preferred method
for defining Bugzilla credentials. Bugzilla API Keys are also required
if you have enabled 2 Factor Authentication in Bugzilla.

For security reasons, the legacy credentials are being removed from the
config.
'''.lstrip()

testedwith = '3.5 3.6 3.7 3.8'
buglink = 'https://bugzilla.mozilla.org/enter_bug.cgi?product=Developer%20Services&component=General'

cmdtable = {}
command = cmdutil.command(cmdtable)

wizardsteps = {
    'hgversion',
    'username',
    'diff',
    'color',
    'historyediting',
    'fsmonitor',
    'firefoxtree',
    'codereview',
    'configchange',
}

@command('configwizard', [
    ('s', 'statedir', '', _('directory to store state')),
    ], _('hg configwizard'), optionalrepo=True)
def configwizard(ui, repo, statedir=None, **opts):
    """Ensure your Mercurial configuration is up to date."""
    runsteps = set(wizardsteps)
    if ui.hasconfig('configwizard', 'steps'):
        runsteps = set(ui.configlist('configwizard', 'steps'))

    hgversion = util.versiontuple(n=3)

    if hgversion < MINIMUM_SUPPORTED_VERSION:
        ui.warn(VERSION_TOO_OLD)
        raise error.Abort('upgrade Mercurial then run again')

    uiprompt(ui, INITIAL_MESSAGE, default='<RETURN>')

    configpaths = [p for p in scmutil.userrcpath() if os.path.exists(p)]
    path = configpaths[0] if configpaths else scmutil.userrcpath()[0]
    cw = configobjwrapper(path)

    if 'hgversion' in runsteps:
        if _checkhgversion(ui, hgversion):
            return 1

    if 'username' in runsteps:
        _checkusername(ui, cw)

    if 'diff' in runsteps:
        _checkdiffsettings(ui, cw)

    if 'color' in runsteps:
        _promptnativeextension(ui, cw, 'color', 'Enable color output to your terminal')

    if 'historyediting' in runsteps:
        _checkhistoryediting(ui, cw)

    if 'fsmonitor' in runsteps:
        _checkfsmonitor(ui, cw, hgversion)

    if 'firefoxtree' in runsteps:
        _promptvctextension(ui, cw, 'firefoxtree', FIREFOXTREE_INFO)

    if 'codereview' in runsteps:
        _checkcodereview(ui, cw)

    if 'configchange' in runsteps:
        return _handleconfigchange(ui, cw)

    return 0


def _checkhgversion(ui, hgversion):
    if hgversion >= OLDEST_NON_LEGACY_VERSION:
        return

    ui.warn(LEGACY_MERCURIAL_MESSAGE % util.version())
    ui.warn('\n')

    if os.name == 'nt':
        ui.warn('Please upgrade to the latest MozillaBuild to upgrade '
                'your Mercurial install.\n\n')
    else:
        ui.warn('Please run `mach bootstrap` to upgrade your Mercurial '
                'install.\n\n')

    if ui.promptchoice('Would you like to continue using an old Mercurial version (Yn)? $$ &Yes $$ &No'):
        return 1


def uiprompt(ui, msg, default=None):
    """Wrapper for ui.prompt() that only renders the last line of text as prompt.

    This prevents entire prompt text from rendering as a special color which
    may be hard to read.
    """
    lines = msg.splitlines(True)
    ui.write(''.join(lines[0:-1]))
    return ui.prompt(lines[-1], default=default)


def uipromptchoice(ui, msg, default=0):
    lines = msg.splitlines(True)
    ui.write(''.join(lines[0:-1]))
    return ui.promptchoice(lines[-1], default=default)


def _checkusername(ui, cw):
    if ui.config('ui', 'username'):
        return

    ui.write(MISSING_USERNAME)

    name, email = None, None

    name = ui.prompt('What is your name?', '')
    if name:
        email = ui.prompt('What is your e-mail address?', '')

    if name and email:
        username = '%s <%s>' % (name, email)
        if 'ui' not in cw.c:
            cw.c['ui'] = {}
        cw.c['ui']['username'] = username.strip()

        ui.write('setting ui.username=%s\n\n' % username)
    else:
        ui.warn('Unable to set username; You will be unable to author '
                'commits\n\n')


def _checkdiffsettings(ui, cw):
    git = ui.configbool('diff', 'git')
    showfunc = ui.configbool('diff', 'showfunc')

    if git and showfunc:
        return

    if not uipromptchoice(ui, BAD_DIFF_SETTINGS):
        if 'diff' not in cw.c:
            cw.c['diff'] = {}

        cw.c['diff']['git'] = 'true'
        cw.c['diff']['showfunc'] = 'true'


def _promptnativeextension(ui, cw, ext, msg):
    if ui.hasconfig('extensions', ext):
        return

    if not uipromptchoice(ui, '%s (Yn) $$ &Yes $$ &No' % msg):
        if 'extensions' not in cw.c:
            cw.c['extensions'] = {}

        cw.c['extensions'][ext] = ''


def _vctextpath(ext, path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ext_dir = os.path.normpath(os.path.join(here, '..'))
    ext_path = os.path.join(ext_dir, ext)
    if path:
        ext_path = os.path.join(ext_path, path)

    return ext_path


def _enableext(cw, name, value):
    if 'extensions' not in cw.c:
        cw.c['extensions'] = {}

    cw.c['extensions'][name] = value


def _promptvctextension(ui, cw, ext, msg):
    if ui.hasconfig('extensions', ext):
        return

    ext_path = _vctextpath(ext)

    # Verify the extension loads before prompting to enable it. This is
    # done out of paranoia.
    result = subprocess.check_output([sys.argv[0],
                                      '--config', 'extensions.testmodule=%s' % ext_path,
                                      '--config', 'ui.traceback=true'],
                                     stderr=subprocess.STDOUT)
    if 'Traceback' in result:
        return

    if uipromptchoice(ui, '%s (Yn) $$ &Yes $$ &No' % msg):
        return

    _enableext(cw, ext, ext_path)


def _checkhistoryediting(ui, cw):
    if all(ui.hasconfig('extensions', e) for e in ('histedit', 'rebase')):
        return

    if ui.promptchoice('Enable history rewriting commands (Yn)? $$ &Yes $$ &No'):
        return

    if 'extensions' not in cw.c:
        cw.c['extensions'] = {}

    cw.c['extensions']['histedit'] = ''
    cw.c['extensions']['rebase'] = ''


def _checkfsmonitor(ui, cw, hgversion):
    # fsmonitor came into existence in Mercurial 3.8. Before that, it
    # was the "hgwatchman" extension from a 3rd party repository.
    # Instead of dealing with installing hgwatchman, we version sniff
    # and print a message about wanting a more modern Mercurial version.

    if ui.hasconfig('extensions', 'fsmonitor'):
        try:
            del cw.c['extensions']['hgwatchman']
            ui.write('Removing extensions.hgwatchman because fsmonitor is installed\n')
        except KeyError:
            pass

        return

    # Mercurial 3.8+ has fsmonitor built-in.
    if hgversion >= (3, 8, 0):
        _promptnativeextension(ui, cw, 'fsmonitor', FSMONITOR_INFO)
    else:
        ui.write(FSMONITOR_NOT_AVAILABLE)


def _checkcodereview(ui, cw):
    # We don't check for bzexport if reviewboard is enabled because
    # bzexport is legacy.
    if ui.hasconfig('extensions', 'reviewboard'):
        return

    if ui.promptchoice('Will you be submitting commits to Mozilla (Yn)? $$ &Yes $$ &No'):
        return

    confrb = False
    answer = uipromptchoice(ui, CODEREVIEW_INFO, default=0) + 1
    if answer in (1, 2):
        _enableext(cw, 'reviewboard', _vctextpath('reviewboard', 'client.py'))
        confrb = True

    if answer in (2, 3):
        _enableext(cw, 'bzexport', _vctextpath('bzexport'))

    # Now verify Bugzilla credentials and other config foo is set.
    bzuser = ui.config('bugzilla', 'username')
    bzapikey = ui.config('bugzilla', 'apikey')

    if not bzuser or not bzapikey:
        ui.write(MISSING_BUGZILLA_CREDENTIALS)

    if not bzuser:
        bzuser = ui.prompt('What is your Bugzilla email address? (optional)', default='')

    if bzuser and not bzapikey:
        ui.write(BUGZILLA_API_KEY_INSTRUCTIONS)
        bzapikey = ui.prompt('Please enter a Bugzilla API Key: (optional)', default='')

    if bzuser or bzapikey:
        if 'bugzilla' not in cw.c:
            cw.c['bugzilla'] = {}

    if bzuser:
        cw.c['bugzilla']['username'] = bzuser
    if bzapikey:
        cw.c['bugzilla']['apikey'] = bzapikey

    if any(ui.hasconfig('bugzilla', c) for c in ('password', 'userid', 'cookie')):
        ui.write(LEGACY_BUGZILLA_CREDENTIALS_DETECTED)

    for c in ('password', 'userid', 'cookie'):
        try:
            del cw.c['bugzilla'][c]
        except KeyError:
            pass

    # TODO configure mozilla.ircnick and the "review" path

def _handleconfigchange(ui, cw):
    # Obtain the old and new content so we can show a diff.
    newbuf = io.BytesIO()
    cw.write(newbuf)
    newbuf.seek(0)
    newlines = [l.rstrip() for l in newbuf.readlines()]
    oldlines = []
    if os.path.exists(cw.path):
        with open(cw.path, 'rb') as fh:
            oldlines = [l.rstrip() for l in fh.readlines()]

    diff = list(difflib.unified_diff(oldlines, newlines,
                                     'hgrc.old', 'hgrc.new',
                                     lineterm=''))

    if len(diff):
        ui.write('Your config file needs updating.\n')
        if not ui.promptchoice('Would you like to see a diff of the changes first (Yn)? $$ &Yes $$ &No'):
            for line in diff:
                ui.write('%s\n' % line)
            ui.write('\n')

        if not ui.promptchoice('Write changes to hgrc file (Yn)? $$ &Yes $$ &No'):
            with open(cw.path, 'wb') as fh:
                fh.write(newbuf.getvalue())
        else:
            ui.write('config changes not written; we would have written the following:\n')
            ui.write(newbuf.getvalue())
            return 1


class configobjwrapper(object):
    """Manipulate config files with ConfigObj.

    Mercurial doesn't support writing config files. ConfigObj does. ConfigObj
    also supports preserving comments in config files, which is user friendly.

    This class provides a mechanism to load and write config files with
    ConfigObj.
    """
    def __init__(self, path):
        self.path = path
        self._random = str(uuid.uuid4())

        lines = []

        if os.path.exists(path):
            with open(path, 'rb') as fh:
                for line in fh:
                    # Mercurial has special syntax to include other files.
                    # ConfigObj doesn't recognize it. Normalize on read and
                    # restore on write to preserve it.
                    if line.startswith('%include'):
                        line = '#%s %s' % (self._random, line)

                    if line.startswith(';'):
                        raise error.Abort('semicolon (;) comments in config '
                                          'files not supported',
                                          hint='use # for comments')

                    lines.append(line)

        self.c = ConfigObj(infile=lines, encoding='utf-8',
                           write_empty_values=True, list_values=False)

    def write(self, fh):
        lines = self.c.write()
        for line in lines:
            if line.startswith('#%s ' % self._random):
                line = line[2 + len(self._random):]

            fh.write('%s\n' % line)

