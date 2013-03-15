#!/usr/bin/python
#
# Copyright 2012 Nextdoor.com, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Daemon that monitors a set of services and updates a ServiceRegistry
with their status.

The purpose of this script is to monitor a given 'service' on a schedule
defined by 'refresh' and register or de-register that service with an Apache
ZooKeeper instance.

The script reads in a config file (default /etc/zk/config.cfg) and parses each
section. Each section begins with a header that defines the service name for
logging purposes, and then contains several config options that tell us how to
monitor the service. Eg:

  [memcache]
  cmd: pgrep memcached
  refresh: 30
  service_port: 11211
  service_hostname: 123.123.123.123
  zookeeper_path: /services/prod-uswest1-mc
  zookeeper_data: { "foo": "bar", "bar": "foo" }

Copyright 2012 Nextdoor Inc.
"""

__author__ = 'matt@nextdoor.com (Matt Wise)'

from sys import stdout, stderr
import optparse
import socket
import subprocess
import threading
import time
import json
import signal
import ConfigParser
import logging
import logging.handlers
import os

# Get our ServiceRegistry class
from nd_service_registry import KazooServiceRegistry as ServiceRegistry
from nd_service_registry import exceptions

# Our default variables
from version import __version__ as VERSION

# Defaults
LOG = '/var/log/zk_watcher.log'
ZOOKEEPER_SESSION_TIMEOUT_USEC = 300000  # microseconds
ZOOKEEPER_URL = 'localhost:2181'

# This global variable is used to trigger the service stopping/starting...
RUN_STATE = True

# First handle all of the options passed to us
usage = 'usage: %prog <options>'
parser = optparse.OptionParser(usage=usage, version=VERSION,
                               add_help_option=True)
parser.set_defaults(verbose=True)
parser.add_option('-c', '--config', dest='config',
                  default='/etc/zk/config.cfg',
                  help='override the default config file (/etc/zk/config.cfg)')
parser.add_option('-s', '--server', dest='server', default=ZOOKEEPER_URL,
                  help='server address (default: localhost:2181')
parser.add_option('-v', '--verbose', action='store_true', dest='verbose',
                  default=False,
                  help='verbose mode')
parser.add_option('-l', '--syslog', action='store_true', dest='syslog',
                  default=False,
                  help='log to syslog')
(options, args) = parser.parse_args()


class WatcherDaemon(threading.Thread):
    """The main daemon process.

    This is the main object that defines all of our major functions and
    connection information."""

    LOGGER = 'WatcherDaemon'

    def __init__(self, server, config_file, verbose=False):
        """Initilization code for the main WatcherDaemon.

        Set up our local logger reference, and pid file locations."""
        # Initiate our thread
        super(WatcherDaemon, self).__init__()

        self.log = logging.getLogger(self.LOGGER)
        self.log.info('WatcherDaemon %s' % VERSION)

        self._watchers = []
        self._sr = None
        self._config_file = config_file
        self._server = server
        self._verbose = verbose

        # Get a logger for nd_service_registry and set it to be quiet
        nd_log = logging.getLogger('nd_service_registry')

        # Set up our threading environment
        self._event = threading.Event()

        # These threads can die with prejudice. Make sure that any time the
        # python interpreter exits, we exit immediately
        self.setDaemon(True)

        # Watch for any signals
        signal.signal(signal.SIGHUP, self._signal_handler)

        # Bring in our configuration options
        self._parse_config()

        # Create our ServiceRegistry object
        self._connect()

        # Start up
        self.start()

    def _signal_handler(self, signum, frame):
        """Watch for certain signals"""
        self.log.warning('Received signal: %s' % signum)
        if signum == 1:
            self.log.warning('Received SIGHUP. Reloading config.')
            self._parse_config()
            self._connect()
            self._setup_watchers()

    def _parse_config(self):
        """Read in the supplied config file and update our local settings."""
        self.log.debug('Loading config...')
        self._config = ConfigParser.ConfigParser()
        self._config.read(self._config_file)

        # Check if auth data was supplied. If it is, read it in and then remove
        # it from our configuration object so its not used anywhere else.
        try:
            self.user = self._config.get('auth', 'user')
            self.password = self._config.get('auth', 'password')
            self._config.remove_section('auth')
        except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
            self.user = None
            self.password = None

    def _connect(self):
        """Connects to the ServiceRegistry.

        If already connected, updates the current connection settings."""

        self.log.debug('Checking for ServiceRegistry object...')
        if not self._sr:
            self.log.debug('Creating new ServiceRegistry object...')
            self._sr = ServiceRegistry(server=self._server, lazy=True,
                                       username=self.user,
                                       password=self.password)
        else:
            self.log.debug('Updating existing object...')
            self._sr.set_username(self.user)
            self._sr.set_password(self.password)

    def _setup_watchers(self):
        # For each watcher, see if we already have one for a given path or not.
        for service in self._config.sections():
            w = self._get_watcher(service)

            # Gather up the config data for our section into a few local
            # variables so that we can shorten the statements below.
            command = self._config.get(service, 'cmd')
            service_port = self._config.get(service, 'service_port')
            zookeeper_path = self._config.get(service, 'zookeeper_path')
            refresh = self._config.get(service, 'refresh')

            # Gather our optional parameters. If they don't exist, set
            # some reasonable default.
            try:
                zookeeper_data = self._parse_data(
                    self._config.get(service, 'zookeeper_data'))
            except:
                zookeeper_data = {}

            try:
                service_hostname = self._config.get(service, 'service_hostname')
            except:
                service_hostname = socket.getfqdn()

            if w:
                # Certain fields cannot be changed without destroying the
                # object and its registration with Zookeeper.
                if w._service_port != service_port or \
                    w._service_hostname != service_hostname or \
                        w._path != zookeeper_path:
                    w.stop()
                    w = None

            if w:
                # We already have a watcher for this service. Update its
                # object data, and let it keep running.
                w.set(command=command,
                      data=zookeeper_data,
                      refresh=refresh)

            # If there's still no 'w' returned (either _get_watcher failed, or
            # we noticed that certain un-updatable fields were changed, then
            # create a new object.
            if not w:
                w = ServiceWatcher(registry=self._sr,
                                   service=service,
                                   service_port=service_port,
                                   service_hostname=service_hostname,
                                   command=command,
                                   path=zookeeper_path,
                                   data=zookeeper_data,
                                   refresh=refresh)
                self._watchers.append(w)

        # Check if any watchers need to be destroyed because they're no longer
        # in our config.
        for w in self._watchers:
            if not w._service in list(self._config.sections()):
                w.stop()
                self._watchers.remove(w)

    def _get_watcher(self, service):
        """Returns a watcher based on the service name."""
        for watcher in self._watchers:
            if watcher._service == service:
                return watcher
        return None

    def _parse_data(self, data):
        """Convert a string of data from ConfigParse into our dict.

        The zookeeper_data field supports one of two types of fields. Either
        a single key=value string, or a JSON-formatted set of key=value
        pairs:

            zookeeper_data: foo=bar
            zookeeper_data: foo=bar, bar=foo
            zookeeper_data: { "foo": "bar", "bar": "foo" }

        Args:
            data: String representing data above"""

        try:
            data_dict = json.loads(data)
        except:
            data_dict = {}
            for pair in data.split(','):
                if pair.split('=').__len__() == 2:
                    key = pair.split('=')[0]
                    value = pair.split('=')[1]
                    data_dict[key] = value
        return data_dict

    def run(self):
        """Start up all of the worker threads and keep an eye on them"""

        self._setup_watchers()

        # Now, loop. Wait for a death signal
        while True and not self._event.is_set():
            self._event.wait(1)

        # At this point we must be exiting. Kill off our above threads
        for w in self._watchers:
            w.stop()

    def stop(self):
        self._event.set()


class ServiceWatcher(threading.Thread):
    """Monitors a particular service definition."""

    LOGGER = 'WatcherDaemon.ServiceWatcher'

    def __init__(self, registry, service, service_port, command, path, data,
                 service_hostname, refresh=15):
        """Initialize the object and begin monitoring the service."""
        # Initiate our thread
        super(ServiceWatcher, self).__init__()

        self._sr = registry
        self._service = service
        self._service_port = service_port
        self._service_hostname = service_hostname
        self._path = path
        self._fullpath = '%s/%s:%s' % (path, service_hostname, service_port)
        self.set(command, data, refresh)
        self.log = logging.getLogger('%s.%s' % (self.LOGGER, self._service))
        self.log.debug('Initializing...')

        self._event = threading.Event()
        self.setDaemon(True)
        self.start()

    def set(self, command, data, refresh):
        """Public method for re-configuring our service checks.

        NOTE: You cannot re-configure the port or server-name currently.

        Args:
            command: (String) command to execute
            data: (String/Dict) configuration data to pass with registration
            refresh: (Int) frequency (in seconds) of check"""

        self._command = command
        self._refresh = int(refresh)
        self._data = data

    def run(self):
        """Monitors the supplied service, and keeps it registered.

        We loop every second, checking whether or not we need to run our
        check. If we do, we run the check. If we don't, we wait until
        we need to, or we receive a stop."""

        last_checked = 0
        self.log.debug('Beginning run() loop')
        while True and not self._event.is_set():
            if time.time() - last_checked > self._refresh:
                self.log.debug('[%s] running' % self._command)

                # First, run our service check command and see what the
                # return code is
                c = Command(self._command, self._service)
                ret = c.run(timeout=90)

                if ret == 0:
                    # If the command was successfull...
                    self.log.debug('[%s] returned successfull' % self._command)
                    self._update(state=True)
                else:
                    # If the command failed...
                    self.log.warning('[%s] returned a failed exit code [%s]' %
                                     (self._command, ret))
                    self._update(state=False)

                # Now that our service check is done, update our lastrun{}
                # array with the current time, so that we can check how
                # long its been since the last run.
                last_checked = time.time()

            # Sleep for one second just so that we dont run in a crazy loop
            # taking up all kinds of resources.
            self._event.wait(1)

        self._update(False)
        self._sr.unset(self._fullpath)
        self._sr = None
        self.log.debug('Watcher %s is exiting the run() loop.' % self._service)

    def stop(self):
        """Stop the run() loop."""
        self._event.set()

    def _update(self, state):
        # Call ServiceRegistry.set() method with our state, data,
        # path information. The ServiceRegistry module will take care of
        # updating the data, state, etc.
        self.log.debug('Attempting to update service [%s] with '
                       'data [%s], and state [%s].' %
                       (self._service, self._data, state))
        try:
            self._sr.set_node(self._fullpath, self._data, state)
            self.log.debug('[%s] sucessfully updated path %s with state %s' %
                          (self._service, self._fullpath, state))
            return True
        except exceptions.NoConnection, e:
            self.log.warn('[%s] could not update path %s with state %s: %s' %
                         (self._service, self._fullpath, state, e))
            return False


class Command(object):
    """Wrapper to run a command with a timeout for safety."""

    LOGGER = 'WatcherDaemon.Command'

    def __init__(self, cmd, service):
        """Initialize the Command object.

        This object can be created once, and run many times. Each time it
        runs we initiate a small thread to run our process, and if that
        process times out, we kill it."""

        self._cmd = cmd
        self._process = None
        self.log = logging.getLogger('%s.%s' % (self.LOGGER, service))

    def run(self, timeout):
        def target():
            self.log.debug('[%s] started...' % self._cmd)
            # Deliberately do not capture any output. Using PIPEs can
            # cause deadlocks according to the Python documentation here
            # (http://docs.python.org/library/subprocess.html)
            #
            # "Warning This will deadlock when using stdout=PIPE and/or
            # stderr=PIPE and the child process generates enough output to
            # a pipe such that it blocks waiting for the OS pipe buffer to
            # accept more # data. Use communicate() to avoid that."
            #
            # We only care about the exit code of the command anyways...
            try:
                self._process = subprocess.Popen(
                    self._cmd.split(' '),
                    shell=False,
                    stdout=open('/dev/null', 'w'),
                    stderr=None,
                    stdin=None)
                self._process.communicate()
            except OSError, e:
                self.log.warn('Failed to run: %s' % e)
                return 1
            self.log.debug('[%s] finished... returning %s' %
                          (self._cmd, self._process.returncode))

        thread = threading.Thread(target=target)
        thread.start()

        thread.join(timeout)
        if thread.is_alive():
            self.log.debug('[%s] taking too long to respond, terminating.' %
                           self._cmd)
            try:
                self._process.terminate()
            except:
                pass
            thread.join()

        # If the subprocess.Popen() fails for any reason, it returns 1... but
        # because its in a thread, we never actually see that error code.
        if self._process:
            return self._process.returncode
        else:
            return 1


def setup_logger():
    """Configure our main logger object"""
    # Get our logger
    logger = logging.getLogger()
    pid = os.getpid()
    format = 'zk_watcher[' + str(pid) + '] [%(name)s] ' \
             '[%(funcName)s]: (%(levelname)s) %(message)s'
    formatter = logging.Formatter(format)

    if options.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if options.syslog:
        handler = logging.handlers.SysLogHandler('/dev/log', 'syslog')
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def main():
    logger = setup_logger()
    watcher = WatcherDaemon(
        config_file=options.config,
        server=options.server,
        verbose=options.verbose)

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info('Exiting')
            break

if __name__ == '__main__':
    main()
