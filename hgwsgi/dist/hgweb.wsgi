#!/usr/bin/env python

config = "/repo/hg/webroot_wsgi/dist/hgweb.config"

from mercurial.hgweb import hgweb

import os
os.environ["HGENCODING"] = "UTF-8"

application = hgweb(config)

