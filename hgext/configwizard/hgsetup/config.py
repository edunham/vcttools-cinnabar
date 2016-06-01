# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this,
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import unicode_literals

HOST_FINGERPRINTS = {
    'bitbucket.org': '3f:d3:c5:17:23:3c:cd:f5:2d:17:76:06:93:7e:ee:97:42:21:14:aa',
    'bugzilla.mozilla.org': '7c:7a:c4:6c:91:3b:6b:89:cf:f2:8c:13:b8:02:c4:25:bd:1e:25:17',
    'hg.mozilla.org': 'af:27:b9:34:47:4e:e5:98:01:f6:83:2b:51:c9:aa:d8:df:fb:1a:27',
}


class MercurialConfig(object):
    """Interface for manipulating a Mercurial config file."""

    def add_mozilla_host_fingerprints(self):
        """Add host fingerprints so SSL connections don't warn."""
        if 'hostfingerprints' not in self._c:
            self._c['hostfingerprints'] = {}

        for k, v in HOST_FINGERPRINTS.items():
            self._c['hostfingerprints'][k] = v

    def update_mozilla_host_fingerprints(self):
        """Update host fingerprints if they are present."""
        if 'hostfingerprints' not in self._c:
            return

        for k, v in HOST_FINGERPRINTS.items():
            if k in self._c['hostfingerprints']:
                self._c['hostfingerprints'][k] = v

    def activate_extension(self, name, path=None):
        """Activate an extension.

        An extension is defined by its name (in the config) and a filesystem
        path). For built-in extensions, an empty path is specified.
        """
        if not path:
            path = ''

        if 'extensions' not in self._c:
            self._c['extensions'] = {}

        self._c['extensions'][name] = path
