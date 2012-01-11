# Copyright (C) 2010 Mozilla Foundation
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""
bzexport

Attach a patch from a HG repository to a bugzilla bug.

To enable this extension, edit your ~/.hgrc (or %APPDATA%/Mercurial.ini)
and add:

    [extensions]

    bzexport = /path/to/bzexport.py

You can then use it like so:
hg bzexport [-e] REV BUG

Where REV is any local revision, and BUG is a bug number on
bugzilla.mozilla.org or the option '--new' to create a new bug. The extension
is tuned to work best with MQ changesets (it can only currently work with
applied patches).

If no revision is specified, it will default to 'tip'. If no
bug is specified, the changeset commit message will be scanned
for a bug number to use.

"""
from mercurial.i18n import _
from mercurial import commands, config, cmdutil, hg, node, util, patch
from hgext import mq
import base64
from cStringIO import StringIO
import json
import os
import time
import platform
import re
import shutil
import sqlite3
import tempfile
import urllib
import urllib2
import urlparse
import pkg_resources
try:
  from cPickle import dump as pickle_dump, load as pickle_load
except:
  from pickle import dump as pickle_dump, load as pickle_load

# This is stolen from buglink.py
bug_re = re.compile(r'''# bug followed by any sequence of numbers, or
                        # a standalone sequence of numbers
                     (
                        (?:
                          bug |
                          b= |
                          # a sequence of 5+ numbers preceded by whitespace
                          (?=\b\#?\d{5,}) |
                          # numbers at the very beginning
                          ^(?=\d)
                        )
                        (?:\s*\#?)(\d+)
                     )''', re.I | re.X)
review_re = re.compile(r'[ra][=?]+([^ ]+)')

BINARY_CACHE_FILENAME = ".bzexport.cache"
INI_CACHE_FILENAME = ".bzexport"

global_cache = None

def urlopen(ui, req):
    """Wraps urllib2.urlopen() to provide error handling."""
    ui.progress('Accessing bugzilla server', None, item=req.get_full_url())
    #ui.debug("%s %s\n" % (req.get_method(), req.get_data()))
    try:
        return urllib2.urlopen(req)
    except urllib2.HTTPError, e:
        msg = ''
        try:
            err = json.load(e)
            msg = err['message']
        except:
            msg = e
            pass

        if msg:
            ui.warn('Error: %s\n' % msg)
        raise

class bzAuth:
    """
    A helper class to abstract away authentication details.  There are two
    allowable types of authentication: userid/cookie and username/password.
    We encapsulate it here so that functions that interact with bugzilla
    need only call the 'auth' method on the token to get a correct URL.
    """
    typeCookie = 1
    typeExplicit = 2
    def __init__(self, userid, cookie, username, password):
        assert (userid and cookie) or (username and password)
        assert not ((userid or cookie) and (username or password))
        if userid:
            self._type = self.typeCookie
            self._userid = userid
            self._cookie = cookie
            self._username = None
        else:
            self._type = self.typeExplicit
            self._username = username
            self._password = password

    def auth(self):
        if self._type == self.typeCookie:
            return "userid=%s&cookie=%s" % (self._userid, self._cookie)
        else:
            return "username=%s&password=%s" % (self._username, self._password)

    def username(self, ui, api_server):
        # This returns and caches the email-address-like username of the user's ID
        if self._type == self.typeCookie and self._username is None:
            url = api_server + "user/%s?%s" % (self._userid, self.auth())
            req = urllib2.Request(url, None,
                                  {"Accept": "application/json",
                                   "Content-Type": "application/json"})
            conn = urlopen(ui, req)
            try:
                user = json.load(conn)
            except Exception, e:
                pass
            if user and user["name"]:
                return user["name"]
            else:
                ui.write_err("Error: couldn't get your username: %s\n" % str(e))
                return None
        else:
            return self._username

def get_global_path(filename):
    path = None
    if platform.system() == "Windows":
        CSIDL_PERSONAL = 5
        path = win_get_folder_path(CSIDL_PERSONAL)
    else:
        path = os.path.expanduser("~")
    if path:
        path = os.path.join(path, filename)
    return path

def store_global_cache():
    fp = open(get_global_path(BINARY_CACHE_FILENAME), "wb")
    pickle_dump(global_cache, fp)
    fp.close()

def load_global_cache(ui, api_server):
    global global_cache
    cache_file = get_global_path(BINARY_CACHE_FILENAME)

    try:
        fp = open(cache_file, "rb");
        global_cache = pickle_load(fp)
    except IOError, e:
        global_cache = { api_server: { 'real_names': {} } }
    except Exception, e:
        raise util.Abort("Error loading user cache: " + str(e))

    return global_cache

def store_user_cache(cache):
    user_cache = get_global_path(INI_CACHE_FILENAME)
    fp = open(user_cache, "wb")
    for section in cache.sections():
        fp.write("[" + section + "]\n")
        for (user, name) in cache.items(section):
            fp.write(user + " = " + name + "\n")
        fp.write("\n")
    fp.close()

def load_user_cache(ui, api_server):
    user_cache = get_global_path(INI_CACHE_FILENAME)
    section = api_server

    c = config.config()

    # Ensure that the cache exists before attempting to use it
    fp = open(user_cache, "a");
    fp.close()

    c.read(user_cache)
    return c

def load_configuration(ui, api_server):
    cache = load_global_cache(ui, api_server).get(api_server)
    now = time.time()
    if 'configuration' in cache and now - cache['configuration_timestamp'] < 24*60*60*7:
        return cache['configuration']

    ui.write("Refreshing configuration cache for " + api_server + "\n")
    url = api_server + "configuration?cached_ok=1";
    req = urllib2.Request(url, None,
                          {"Accept": "application/json",
                           "Content-Type": "application/json"})
    conn = urlopen(ui, req)
    try:
        configuration = json.load(conn)
    except Exception, e:
        raise util.Abort("Error loading bugzilla configuration: " + str(e))

    cache['configuration'] = configuration
    cache['configuration_timestamp'] = now
    store_global_cache()
    return configuration

def review_flag_type_id(ui, api_server):
    configuration = load_configuration(ui, api_server)
    if not configuration or not configuration["flag_type"]:
        ui.write_err("Error: couldn't find configuration object\n");
        return None

    flag_ids = []
    for flag_id, flag in configuration["flag_type"].iteritems():
        if flag["name"] == "review":
            flag_ids += flag_id
    if not flag_ids:
        ui.write_err("Error: couldn't find review flag id\n")
        return None

    return flag_ids

def create_bug(ui, api_server, token, product, component, version, title, description):
    """
    Create a bugzilla bug using BzAPI.
    """
    url = api_server + "bug?%s" % (token.auth(),)
    json_data = {'product'  : product,
                 'component': component,
                 'summary'  : title,
                 'version'  : version,
                 'comments' : [{ 'text': description }],
                 'op_sys'   : 'All',
                 'platform' : 'All',
                 }

    req = urllib2.Request(url, json.dumps(json_data),
                          {"Accept": "application/json",
                           "Content-Type": "application/json"})
    return urlopen(ui, req)

def create_attachment(ui, api_server, token, bug,
                      attachment_contents, description="attachment",
                      filename="attachment", comment="", reviewers=None):
    """
    Post an attachment to a bugzilla bug using BzAPI.

    """
    attachment = base64.b64encode(attachment_contents)
    url = api_server + "bug/%s/attachment?%s" % (bug, token.auth())

    json_data = {'data': attachment,
                 'encoding': 'base64',
                 'file_name': filename,
                 'description': description,
                 'is_patch': True,
                 'content_type': 'text/plain'}
    if reviewers is not None:
        flag_ids = review_flag_type_id(ui, api_server)
        
        flags = []
        for flag_type_id in flag_ids:
            flags.append({"name": "review",
                          "requestee": {"name": ", ".join(reviewers)},
                          "setter": {"name": token.username(ui, api_server)},
                          "status": "?",
                          "type_id": flag_type_id})
        json_data["flags"] = flags
    if comment:
        json_data["comments"] = [{'text': comment}]

    attachment_json = json.dumps(json_data)
    req = urllib2.Request(url, attachment_json,
                          {"Accept": "application/json",
                           "Content-Type": "application/json"})
    conn = urlopen(ui, req)
    return conn

def win_get_folder_path(folder):
    # Use SHGetFolderPath
    import ctypes
    SHGetFolderPath = ctypes.windll.shell32.SHGetFolderPathW
    SHGetFolderPath.argtypes = [ctypes.c_void_p,
                                ctypes.c_int,
                                ctypes.c_void_p,
                                ctypes.c_int32,
                                ctypes.c_wchar_p]
    path_buf = ctypes.create_unicode_buffer(1024)
    if SHGetFolderPath(0, folder, 0, 0, path_buf) != 0:
        return None

    return path_buf.value

def find_profile(ui):
    """
    Find the default Firefox profile location. Returns None
    if no profile could be located.

    """
    path = None
    if platform.system() == "Darwin":
        # Use FSFindFolder
        from Carbon import Folder, Folders
        pathref = Folder.FSFindFolder(Folders.kUserDomain,
                                      Folders.kApplicationSupportFolderType,
                                      Folders.kDontCreateFolder)
        basepath = pathref.FSRefMakePath()
        path = os.path.join(basepath, "Firefox")
    elif platform.system() == "Windows":
        CSIDL_APPDATA = 26
        path = win_get_folder_path(CSIDL_APPDATA)
        if path:
            path = os.path.join(path, "Mozilla", "Firefox")
    else: # Assume POSIX
        # Pretty simple in comparison, eh?
        path = os.path.expanduser("~/.mozilla/firefox")
    if path is None:
        ui.write_err("Error: couldn't find a Firefox profile.\n")
        return None

    profileini = os.path.join(path, "profiles.ini")
    c = config.config()
    c.read(profileini)
    profile = None
    for section in c.sections():
        if section == "General":
            continue
        if c.get(section, "Default", None) is not None or profile is None:
            profile = c.get(section, "Path", None)
            if c.get(section, "IsRelative", "0") == "1":
                profile = os.path.join(path, profile)
    if profile is None:
        ui.write_err("Error: couldn't find a Firefox profile.\n")
        return None
    return profile

# Choose the cookie to use based on how much of its path matches the URL.
# Useful if you happen to have cookies for both
# https://landfill.bugzilla.org/bzapi_sandbox/ and
# https://landfill.bugzilla.org/bugzilla-3.6-branch/, for example.
def matching_path_len(cookie_path, url_path):
    return len(cookie_path) if url_path.startswith(cookie_path) else 0

def get_cookies_from_profile(ui, profile, bugzilla):
    """
    Given a Firefox profile, try to find the login cookies
    for the given bugzilla URL.

    """
    cookies = os.path.join(profile, "cookies.sqlite")
    if not os.path.exists(cookies):
        return None, None

    # Get bugzilla hostname
    host = urlparse.urlparse(bugzilla).hostname
    path = urlparse.urlparse(bugzilla).path

    # Firefox locks this file, so if we can't open it (browser is running)
    # then copy it somewhere else and try to open it.
    tempdir = None
    try:
        tempdir = tempfile.mkdtemp()
        tempcookies = os.path.join(tempdir, "cookies.sqlite")
        shutil.copyfile(cookies, tempcookies)
        # Firefox uses sqlite's WAL feature, which bumps the sqlite
        # version number. Older sqlites will refuse to open the db,
        # but the actual format is the same (just the journalling is different).
        # Patch the file to give it an older version number so we can open it.
        with open(tempcookies, 'r+b') as f:
            f.seek(18, 0)
            f.write("\x01\x01")
        conn = sqlite3.connect(tempcookies)
        logins = conn.execute("select value, path from moz_cookies where name = 'Bugzilla_login' and (host = ? or host = ?)", (host, "." + host)).fetchall()
        row = sorted(logins, key = lambda row: -matching_path_len(row[1], path))[0]
        login = row[0]
        cookie = conn.execute("select value from moz_cookies "
                              "where name = 'Bugzilla_logincookie' "
                              " and (host = ? or host= ?) "
                              " and path = ?",
                              (host, "." + host, row[1])).fetchone()[0]
        ui.debug("host=%s path=%s login=%s cookie=%s\n" % (host, row[1], login, cookie))
        if isinstance(login, unicode):
            login = login.encode("utf-8")
            cookie = cookie.encode("utf-8")
        return login, cookie
    except Exception, e:
        ui.write_err("Error: failed to get bugzilla login cookies from "
                     "Firefox profile at %s: %s\n" % (profile, str(e)))
        return None, None
    finally:
        if tempdir:
            shutil.rmtree(tempdir)

def get_default_version(ui, api_server, product):
    c = load_configuration(ui, api_server)
    versions = c['product'].get(product, {}).get('version')
    if versions is None:
        raise util.Abort(_("Product %s has no versions") % product)
    if versions:
        return versions[-1]

class PUTRequest(urllib2.Request):
    def get_method(self):
        return "PUT"

def obsolete_old_patches(ui, api_server, token, bug, filename, ignore_id):
    url = api_server + "bug/%s/attachment?%s" % (bug, token.auth()) 
    req = urllib2.Request(url, None,
                          {"Accept": "application/json",
                           "Content-Type": "application/json"})
    conn = urlopen(ui, req)
    try:
        bug = json.load(conn)
    except Exception, e:
        ui.write_err("Error: couldn't load info for bug " + bug + ": %s\n" % str(e))
        return False

    patches = [p for p in bug["attachments"] if p["is_patch"] and not p["is_obsolete"] and p["file_name"] == filename and int(p["id"]) != int(ignore_id)]
    if not len(patches):
        return True

    for p in patches:
        #TODO: "?last_change_time=" + p["last_change_time"] to avoid conflicts?
        url = api_server + "attachment/%s?%s" % (str(p["id"]), token.auth())

        attachment_data = p
        attachment_data["is_obsolete"] = True
        attachment_json = json.dumps(attachment_data)
        req = PUTRequest(url, attachment_json,
                         {"Accept": "application/json",
                          "Content-Type": "application/json"})
        conn = urlopen(ui, req)
        try:
            result = json.load(conn)
        except Exception, e:
            ui.write_err("Error: couldn't update attachment " + p["id"] + ": %s\n" % e)
            return False

    return True

def find_reviewers(ui, api_server, token, search_strings):
    c = load_user_cache(ui, api_server)
    section = api_server

    search_results = []
    for search_string in search_strings:
        name = c.get(section, search_string)
        if name:
            search_results.append({"search_string": search_string,
                                   "names": [name],
                                   "real_names": ["not_a_real_name"]})
            continue

        url = api_server + "user?match=%s&%s" % (search_string, token.auth()) 
        try:
            req = urllib2.Request(url, None,
                                  {"Accept": "application/json",
                                   "Content-Type": "application/json"})
            conn = urlopen(ui, req)
            users = json.load(conn)
            error = None
            name = None
            real_names = map(lambda user: "%s <%s>" % (user["real_name"], user["email"]) if user["real_name"] else user["email"], users["users"])
            names = map(lambda user: user["name"], users["users"])
            search_results.append({"search_string": search_string,
                                   "names": names,
                                   "real_names": real_names})
            if len(real_names) == 1:
                c.set(section, search_string, names[0])
        except Exception, e:
            search_results.append({"search_string": search_string,
                                   "error": str(e),
                                   "real_names": None})
            raise
    store_user_cache(c)
    return search_results

def prompt_menu(ui, name, values, readable_values = None, message = '', allow_none=False):
    prompts = []
    for i in range(0, len(values)):
        prompts.append("&%d" % (i + 1))
        value = (readable_values or values)[i]
        message += "  %d. %s\n" % ((i + 1), value.encode('utf-8', 'replace'))
    if allow_none:
        prompts.append("&none")
        message += "  n. None\n\n"
    prompts.append("&abort")
    message += "  a. Abort\n\n"
    message += _("Select %s:") % name

    choice = ui.promptchoice(message, prompts, len(prompts) - 1)
    if allow_none and choice == len(prompts) - 2:
        return None
    if choice == len(prompts) - 1:
        raise util.Abort("User requested abort while choosing %s" % name)
    else:
        return values[choice]

def product_menu(ui, api_version):
    c = load_configuration(ui, api_version)
    return prompt_menu(ui, 'product', c['product'].keys(), message = "Products available:\n")

def component_menu(ui, api_version, product):
    c = load_configuration(ui, api_version)
    components = c['product'].get(product, {}).get('component', {}).keys()
    if len(components) == 0:
        raise util.Abort("Invalid product '%s' (has no components)" % product)
    return prompt_menu(ui, 'component', components,
                       message = "Components of product %s:\n" % product)

def multi_reviewer_prompt(ui, search_result):
    for n in search_results['real_names']:
        ui.write("Encoding %s...\n" % n)
    return prompt_menu(ui, 'reviewer', search_results['names'],
                       readable_values = search_results['real_names'],
                       message = "Multiple bugzilla users matching \"%s\":\n\n" % search_result["search_string"],
                       allow_none = True)

def validate_reviewers(ui, api_server, auth, search_strings, multi_callback):
    search_results = find_reviewers(ui, api_server, auth, search_strings)
    search_failed = False
    reviewers = []
    for search_result in search_results:
        if search_result["real_names"] is None:
            ui.write_err("Error: couldn't find user with search string \"%s\": %s\n" % (search_result["search_string"], search_result["error"]))
            search_failed = True
        elif len(search_result["real_names"]) > 10:
            ui.write_err("Error: too many bugzilla users matching \"%s\":\n\n" % search_result["search_string"])
            for real_name in search_result["real_names"]:
                ui.write_err("  %s\n" % real_name.encode('ascii', 'replace'))
            search_failed = True
        elif len(search_result["real_names"]) > 1:
            reviewer = multi_callback(ui, search_result)
            if reviewer is not None:
                reviewers.append(reviewer)
        elif len(search_result["real_names"]) == 1:
            reviewers.append(search_result["names"][0])
        else:
            ui.write_err("Couldn't find a bugzilla user matching \"%s\"!\n" % search_result["search_string"])
            search_failed = True
    if search_failed:
        return
    return reviewers

# Copied from savecommitmessage in localrepo.py (but with variable filename)
def savefile(repo, basename, text):
    fp = repo.opener(basename, 'wb')
    try:
        fp.write(text)
    finally:
        fp.close()
    return repo.pathto(fp.name[len(repo.root)+1:])

# Sure sign of a poor developer: they implement their own half-assed, one-off
# templating engine instead of reusing an existing one.

# Simple templating engine: scan a template for @KEYWORDS@ (keywords surrounded
# in @ signs). First, replace them with corresponding values in the 'fields'
# dictionary and show the result to the user. Allow user to edit. Then convert
# the whole template into a regex with /(.*?)/ in place of each keyword and
# match the edited output against that. Pull out the possibly-updated field
# values.
templates = { 'new_bug_template': '''Title: @BUGTITLE@
Product: @PRODUCT@
Component: @COMPONENT@
Version: @PRODVERSION@

Bug Description (aka comment 0):

@BUGCOMMENT0@

--- END Bug Description ---

Attachment Filename: @ATTACHMENT_FILENAME@
Attachment Description: @ATTACHMENT_DESCRIPTION@
Reviewer: @REVIEWER_1@
Reviewer: @REVIEWER_2@
Attachment Comment (appears as a regular comment on the bug):

@ATTACHCOMMENT@

---- END Attachment Comment ----
''',
              'existing_bug_template': '''Bug: @BUGNUM@

Attachment Filename: @ATTACHMENT_FILENAME@
Attachment Description: @ATTACHMENT_DESCRIPTION@
Reviewer: @REVIEWER_1@
Reviewer: @REVIEWER_2@
Attachment Comment (appears as a regular comment on the bug):

@ATTACHCOMMENT@

---- END Attachment Comment ----
''' }

field_re = re.compile(r'@([^@]+)@')
def edit_form(ui, repo, fields, template_name):
    template_fields = []
    def substitute_field(m):
        field_name = m.group(1)
        template_fields.append(field_name)
        return fields[field_name] or '<none>'

    # Fill in a template with the passed-in fields
    template = templates[template_name]
    orig = field_re.sub(substitute_field, template)

    # Convert "template with @KEYWORD1@ and @KEYWORD2@" into
    # "template with (.*?) and (.*?)"
    pattern = re.sub(r'[^\w@]', lambda m: '\\' + m.group(0), template)
    pattern = re.compile(field_re.sub('(.*?)', pattern), re.S)

    # Allow user to edit the form
    new = ui.edit(orig, ui.username())

    ui.write("saved edited form in " + savefile(repo, "last_bzexport.txt", new) + "\n")

    # Use the previously-created pattern to pull out the new keyword values
    m = pattern.match(new)

    new_fields = fields.copy()
    for field, value in zip(template_fields, m.groups()):
        if value == '<required>':
            raise util.Abort("Required field %s not filled in" % (field,))
        elif value == '<none>':
            new_fields[field] = None
        else:
            new_fields[field] = value

    return new_fields

def edit_value(ui, value, desc):
    value = (value or '') + "\nHG: Enter %s.\nHG: Lines starting with 'HG:' will be removed.\n" % desc
    value = ui.edit(value, ui.username())
    value = re.sub("(?m)^HG:.*\n", "", value)
    if not value.strip():
        raise util.Abort("Empty value: %s" % desc)
    return value

def bugzilla_info(ui):
    api_server = ui.config("bzexport", "api_server", "https://api-dev.bugzilla.mozilla.org/latest/")
    bugzilla = ui.config("bzexport", "bugzilla", "https://bugzilla.mozilla.org/")
    username = ui.config("bzexport", "username", None)
    if username:
        username = urllib.quote(username)
    password = ui.config("bzexport", "password", None)
    if password:
        password = urllib.quote(password)
    userid = None
    cookie = None
    #TODO: allow overriding profile location via config
    #TODO: cache cookies?
    if not username:
        profile = find_profile(ui)
        if profile is None:
            return

        userid, cookie = get_cookies_from_profile(ui, profile, bugzilla)
        if userid is None or cookie is None:
            ui.write_err("Error: couldn't find bugzilla login cookies.\n")
            return

    auth = bzAuth(userid, cookie, username, password)

    return (auth, api_server, bugzilla)

def bzexport(ui, repo, *args, **opts):
    """
    Export changesets to bugzilla attachments.

    When the --new option is used, a menu will be displayed for the product and
    component unless a default has been set in the [bzexport] of the config
    file (keys are 'product' and 'component'), or if something has been
    specified on the command line.

    Also, the -e option may be used to bring up an editor that will allow
    editing all fields of the attachment and bug (if creating one).
    """
    auth, api_server, bugzilla = bugzilla_info(ui)

    if opts['no_attachment'] and not opts['new']:
        raise util.Abort(_("--no-attachment requires --new"))
    if opts['no_attachment'] and opts['review']:
        raise util.Abort(_("--reviewers not allowed with --no-attachment"))

    rev = None
    bug = None
    if len(args) < 2:
        # We need to guess at some args.
        if len(args) == 1:
            # Just one arg. Could be a revision or a bug number.
            # Check this first, because a series of digits
            # can be a revision number, but it's unlikely a user
            # would use it to mean a revision here.
            if not args[0].isdigit() and args[0] in repo:
                # Looks like a changeset
                rev = args[0]
            else:
                # Don't do any validation here, to allow
                # users to use bug aliases. The BzAPI call
                # will fail with bad bug numbers.
                bug = args[0]

        # With zero args we'll guess at both, and if we fail we'll
        # fail later.
    elif len(args) > 2:
        ui.write_err("Too many arguments to bzexport!\n")
        return
    else:
        # Just right.
        rev, bug = args

    if rev is None:
        # Default to 'tip'
        rev = 'tip'

    if repo[rev] == repo["tip"]:
        m, a, r, d = repo.status()[:4]
        if (m or a or r or d):
            ui.write_err("Local changes found; refresh first!\n");
            return

    if rev in ["tip", "qtip"]:
        # Look for a nicer name in the MQ.
        if hasattr(repo, 'mq') and repo.mq.applied:
            rev = repo.mq.applied[-1].name

    contents = StringIO()
    diffopts = patch.diffopts(ui, opts)
    context = ui.config("bzexport", "unified", None)
    if context:
        diffopts.context = int(context)
    if hasattr(cmdutil, "export"):
        cmdutil.export(repo, [rev], fp=contents,
                       opts=diffopts)
    else:
        # Support older hg versions
        patch.export(repo, [rev], fp=contents,
                     opts=diffopts)

    # Just always use the rev name as the patch name. Doesn't matter much,
    # unless you want to avoid obsoleting existing patches when uploading a
    # version that doesn't include whitespace changes.
    filename = rev
    if opts['ignore_all_space']:
        filename += "_ws"

    patch_comment = None
    reviewers = []
    desc = opts['description'] or repo[rev].description()
    if not desc or desc.startswith('[mq]'):
        desc = '<required>'
    else:
        # Lightly reformat changeset messages into attachment descriptions.
        # First, strip off any leading "bug NNN" or "b=NNN",
        # but save it in case a bug number was not provided.
        bzexport.newbug = None
        def dosub(m):
            bzexport.newbug = m.group(2)
            return ''
        desc = bug_re.sub(dosub, desc, 1)
        if not bzexport.newbug:
            # Try to find it in the original revision description, if
            # it wasn't found in desc.
            bug_re.sub(dosub, repo[rev].description(), 1)
        if bzexport.newbug:
            if bug and bug != bzexport.newbug:
                ui.warn("Warning: Bug number %s from commandline doesn't match "
                        "bug number %s from changeset description\n"
                        % (bug, bzexport.newbug))
            else:
                bug = bzexport.newbug

        # Next strip any remaining leading separator with whitespace,
        # if the original was something like "bug NNN - "
        desc = desc.lstrip()
        if desc[0] in ['-', ':', '.']:
            desc = desc[1:].lstrip()

        # Next strip off review and approval annotations
        def grab_reviewer(m):
            reviewers.append(m.group(1))
            return ''
        desc = review_re.sub(grab_reviewer, desc).rstrip()

        # Finally, just take the first line in case. If there is more than one
        # line, use it as a comment.
        m = re.match(r'([^\n]*)\n+(.*)', desc, re.DOTALL)
        if m:
            desc = m.group(1)
            patch_comment = m.group(2)

    attachment_comment = opts['comment']
    bug_comment = opts['bug_description']

    if not attachment_comment:
        # New bugs get first shot at the patch comment
        if not opts['new']:
            attachment_comment = patch_comment
        elif bug_comment:
            attachment_comment = patch_comment

    if not bug_comment:
        if opts['new']:
            bug_comment = patch_comment

    if opts["review"]:
        search_strings = opts["review"].split(",")
        reviewers = validate_reviewers(ui, api_server, auth, search_strings, multi_reviewer_prompt)
    elif len(reviewers) > 0:
        # Pulled reviewers out of commit message
        reviewers = validate_reviewers(ui, api_server, auth, reviewers, multi_reviewer_prompt)

    if reviewers is None:
        raise util.Abort("Invalid reviewers")

    values = { 'BUGNUM': bug,
               'BUGTITLE': opts['title'] or desc,
               'PRODUCT': opts.get('product', '') or ui.config("bzexport", "product", '<choose-from-menu>'),
               'COMPONENT': opts.get('component', '') or ui.config("bzexport", "component", '<choose-from-menu>'),
               'PRODVERSION': opts.get('prodversion', '') or ui.config("bzexport", "prodversion", '<default>'),
               'BUGCOMMENT0': bug_comment,
               'ATTACHMENT_FILENAME': filename,
               'ATTACHMENT_DESCRIPTION': desc,
               'ATTACHCOMMENT': attachment_comment,
               }
    try:
        values['REVIEWER_1'] = reviewers[0]
    except:
        values['REVIEWER_1'] = '<none>'
    try:
        values['REVIEWER_2'] = reviewers[1]
    except:
        values['REVIEWER_2'] = '<none>'

    if opts['edit']:
        if opts['new']:
            if values['PRODVERSION'] == '<default>' and values['PRODUCT'] not in [None, '<choose-from-menu>']:
                values['PRODVERSION'] = get_default_version(ui, api_server, values['PRODUCT'])
            values = edit_form(ui, repo, values, 'new_bug_template')
        else:
            values = edit_form(ui, repo, values, 'existing_bug_template')
            if bug != values['BUGNUM']:
                raise util.Abort("Modifying bug number is not supported")

        search_strings = [r for r in [values['REVIEWER_1'], values['REVIEWER_2']]
                            if r is not None ]
        reviewers = validate_reviewers(ui, api_server, auth, search_strings, multi_reviewer_prompt)
        if reviewers is None:
            raise util.Abort("Invalid reviewers")
    else:
        if not opts['no_attachment'] and not values['ATTACHMENT_DESCRIPTION']:
            values['ATTACHMENT_DESCRIPTION'] = ui.prompt(_("Patch description:"), default=filename)

    if opts["new"]:
        if bug is not None:
            raise util.Abort("Bug %s given but creation of new bug requested!" % bug)

        if values['PRODUCT'] in [None, '<choose-from-menu>']:
            values['PRODUCT'] = product_menu(ui, api_server)
        if values['COMPONENT'] in [None, '<choose-from-menu>']:
            values['COMPONENT'] = component_menu(ui, api_server, values['PRODUCT'])
        if values['PRODVERSION'] in [None, '<default>']:
            values['PRODVERSION'] = get_default_version(ui, api_server, values['PRODUCT'])
            ui.write("Using default version %s of product %s\n" % (values['PRODVERSION'], values['PRODUCT']))

        try:
            response = create_bug(ui, api_server, auth,
                                  product = values['PRODUCT'],
                                  component = values['COMPONENT'],
                                  version = values['PRODVERSION'],
                                  title = values['BUGTITLE'],
                                  description = values['BUGCOMMENT0'])
            result = json.load(response)
            bug = result['id']
            ui.write("Created bug %s at %s\n" % (bug, bugzilla + "/show_bug.cgi?id=" + bug))
        except Exception, e:
            raise util.Abort(_("Error creating bug: %s\n" % str(e)))
    else:
        if bug is None:
            raise util.Abort(_("No bug number specified and no bug number "
                               "listed in changeset message!"))

    if opts['no_attachment']:
        return

    if len(reviewers) > 0:
        for reviewer in reviewers:
            ui.write("Requesting review from " + reviewer + "\n")

    result_id = None
    try:
        attach = create_attachment(ui, api_server, auth,
                                   bug, contents.getvalue(),
                                   filename=values['ATTACHMENT_FILENAME'],
                                   description=values['ATTACHMENT_DESCRIPTION'],
                                   comment=values['ATTACHCOMMENT'],
                                   reviewers=reviewers)
        result = json.load(attach)
        attachment_url = urlparse.urljoin(bugzilla,
                                          "attachment.cgi?id=" + result["id"] + "&action=edit")
        print "%s uploaded as %s" % (rev, attachment_url)
        result_id = result["id"]

    except Exception, e:
        raise util.Abort("Error sending patch: %s\n" % str(e))

    if not result_id or not obsolete_old_patches(ui, api_server, auth, bug, filename, result_id):
        return

cmdtable = {
    'bzexport':
        (bzexport,
         [('d', 'description', '', 'Bugzilla attachment description'),
          ('c', 'comment', '', 'Comment to add with the attachment'),
          ('e', 'edit', False,
           'Open a text editor to modify bug fields'),
          ('r', 'review', '',
           'List of users to request review from (comma-separated search strings)'),
          ('', 'new', False,
           'Create a new bug'),
          ('', 'title', '',
           'New bug title'),
          ('', 'product', '',
           'New bug product'),
          ('', 'component', '',
           'New bug component'),
          ('', 'prodversion', '',
           'New bug product version'),
          ('', 'bug-description', '',
           'New bug description (aka comment 0)'),
          ('', 'no-attachment', False,
           'Do not attach anything to the bug (requires --new)'),
          # The following option is passed through directly to patch.diffopts
          ('w', 'ignore_all_space', False, 'Generate a diff that ignores whitespace changes')],
        _('hg bzexport [options] [REV] [BUG]')),
}
