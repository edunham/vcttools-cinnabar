# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import signal
import socket
import subprocess
import time

import psutil

from vcttesting.bugzilla import Bugzilla
from vcttesting.docker import (
    Docker,
    params_from_env,
)
from vcttesting.reviewboard import MozReviewBoard

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))

def get_available_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    host, port = s.getsockname()
    s.close()

    return port

def kill(pid):
    os.kill(pid, signal.SIGINT)

    while psutil.pid_exists(pid):
        time.sleep(0.1)

class MozReview(object):
    """Interface to MozService service.

    This class can be used to create and control MozReview instances.
    """

    def __init__(self, path):
        if not path:
            raise Exception('You must specify a path to create an instance')
        path = os.path.abspath(path)
        self._path = path

        self._name = os.path.dirname(path)

        if not os.path.exists(path):
            os.mkdir(path)

        self._state_path = os.path.join(path, 'state.json')

        docker_state = os.path.join(path, 'docker-state.json')

        self._docker_state = docker_state

        docker_url, tls = params_from_env(os.environ)
        self._docker = Docker(docker_state, docker_url, tls=tls)

        if not self._docker.is_alive():
            raise Exception('Docker is not available.')

        self.bugzilla_username = None
        self.bugzilla_password = None

        if os.path.exists(self._state_path):
            with open(self._state_path, 'rb') as fh:
                state = json.load(fh)

                for k, v in state.items():
                    setattr(self, k, v)

    def get_bugzilla(self, username=None, password=None):
        username = username or self.bugzilla_username or 'admin@example.com'
        password = password or self.bugzilla_password or 'password'

        return Bugzilla(self.bugzilla_url, username=username, password=password)

    def start(self, bugzilla_port=None, reviewboard_port=None,
            mercurial_port=None, pulse_port=None, verbose=False,
            db_image=None, web_image=None, pulse_image=None):
        """Start a MozReview instance."""
        if not bugzilla_port:
            bugzilla_port = get_available_port()
        if not reviewboard_port:
            reviewboard_port = get_available_port()
        if not mercurial_port:
            mercurial_port = get_available_port()
        if not pulse_port:
            pulse_port = get_available_port()

        mr_info = self._docker.start_mozreview(cluster=self._name,
                hostname=None, http_port=bugzilla_port, pulse_port=pulse_port,
                db_image=db_image, web_image=web_image,
                pulse_image=pulse_image, verbose=verbose)

        bugzilla_url = mr_info['bugzilla_url']

        self.bugzilla_url = bugzilla_url
        bugzilla = self.get_bugzilla()

        rb = MozReviewBoard(self._path, bugzilla_url=bugzilla_url,
            pulse_host=mr_info['pulse_host'], pulse_port=mr_info['pulse_port'])
        rb.create()
        reviewboard_pid = rb.start(reviewboard_port)

        reviewboard_url = 'http://localhost:%s/' % reviewboard_port

        self.bugzilla_url = bugzilla_url
        self.reviewboard_url = reviewboard_url
        self.reviewboard_pid = reviewboard_pid
        self.admin_username = bugzilla.username
        self.admin_password = bugzilla.password
        self.pulse_host = mr_info['pulse_host']
        self.pulse_port = mr_info['pulse_port']

        mercurial_pid = self._start_mercurial_server(mercurial_port)

        self.mercurial_url = 'http://localhost:%s/' % mercurial_port
        with open(self._hg_url_path, 'w') as fh:
            fh.write(self.mercurial_url)

        self.mercurial_pid = mercurial_pid

        state = {
            'bugzilla_url': bugzilla_url,
            'reviewboard_url': reviewboard_url,
            'reviewboard_pid': reviewboard_pid,
            'mercurial_url': self.mercurial_url,
            'mercurial_pid': mercurial_pid,
            'admin_username': bugzilla.username,
            'admin_password': bugzilla.password,
            'pulse_host': mr_info['pulse_host'],
            'pulse_port': mr_info['pulse_port'],
        }

        with open(self._state_path, 'wb') as fh:
            json.dump(state, fh, indent=2, sort_keys=True)

    def stop(self):
        """Stop all services associated with this MozReview instance."""
        if os.path.exists(self._hg_pid_path):
            with open(self._hg_pid_path, 'rb') as fh:
                pid = int(fh.read().strip())
                kill(pid)

            os.unlink(self._hg_pid_path)

        rb = MozReviewBoard(self._path)
        rb.stop()

        self._docker.stop_bmo(self._name)

    def create_repository(self, path):
        with open(self._hg_url_path, 'rb') as fh:
            url = '%s%s' % (fh.read(), path)

        full_path = os.path.join(self._path, 'repos', path)

        env = dict(os.environ)
        env['HGRCPATH'] = '/dev/null'
        subprocess.check_call([self._hg, 'init', full_path], env=env)

        rb = MozReviewBoard(self._path)
        rbid = rb.add_repository(os.path.dirname(path), url)

        with open(os.path.join(full_path, '.hg', 'hgrc'), 'w') as fh:
            fh.write('\n'.join([
                '[reviewboard]',
                'repoid = %s' % rbid,
                '',
            ]))

        return url, rbid

    def create_user(self, email, password, fullname):
        b = self.get_bugzilla()
        return b.create_user(email, password, fullname)

    @property
    def _hg_pid_path(self):
        return os.path.join(self._path, 'hg.pid')

    @property
    def _hg_url_path(self):
        return os.path.join(self._path, 'hg.url')

    @property
    def _hg(self):
        return os.path.join(ROOT, 'venv', 'bin', 'hg')

    def _start_mercurial_server(self, port):
        repos_path = os.path.join(self._path, 'repos')
        if not os.path.exists(repos_path):
            os.mkdir(repos_path)

        rb_ext_path = os.path.join(ROOT, 'hgext', 'reviewboard', 'server.py')
        dummyssh = os.path.join(ROOT, 'pylib', 'mercurial-support', 'dummyssh')

        global_hgrc = os.path.join(self._path, 'hgrc')
        with open(global_hgrc, 'w') as fh:
            fh.write('\n'.join([
                '[phases]',
                'publish = False',
                '',
                '[ui]',
                'ssh = python "%s"' % dummyssh,
                '',
                '[reviewboard]',
                'url = %s' % self.reviewboard_url,
                '',
                '[extensions]',
                'reviewboard = %s' % rb_ext_path,
                '',
                '[bugzilla]',
                'url = %s' % self.bugzilla_url,
                '',
                '[web]',
                'push_ssl = False',
                'allow_push = *',
                '',
                '[paths]',
                '/ = %s/**' % repos_path,
                '',
            ]))

        env = os.environ.copy()
        env['HGRCPATH'] = '/dev/null'
        env['HGENCODING'] = 'UTF-8'
        args = [
            self._hg,
            'serve',
            '-d',
            '-p', str(port),
            '--pid-file', self._hg_pid_path,
            '--web-conf', global_hgrc,
            '--accesslog', os.path.join(self._path, 'hg.access.log'),
            '--errorlog', os.path.join(self._path, 'hg.error.log'),
        ]
        # We execute from / so Mercurial doesn't pick up config files
        # from parent directories.
        subprocess.check_call(args, env=env, cwd='/')

        with open(self._hg_pid_path, 'rb') as fh:
            pid = fh.read().strip()

        return pid
