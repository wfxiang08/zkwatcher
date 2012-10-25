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

"""Daemon that monitors a set of services and updates ZooKeeper with their status.

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
  zookeeper_path: /services/prod-uswest1-mc
  zookeeper_data: foo=bar

During the main runtime of the code we loop over the sections one by one. Each time
we check a section, we double check when the last time it ran was compared to the
'refresh' variable that is defined. If 'refresh' is less than 'lastrun', we run the check
and update ZooKeeper accordingly.

Copyright 2011 Nextdoor Inc.
References: http://code.activestate.com/recipes/66012/
Advanced Programming in the Unix Environment by W. Richard Stevens
"""

__author__ = 'matt@nextdoor.com (Matt Wise)'

from sys import stdout, stderr
import daemon
import daemon.runner
import re
import optparse
import socket
import subprocess
import time
import signal
import ConfigParser
import zc.zk
import logging
import logging.handlers
import os
import sys
import zookeeper

# Our default variables
from version import __version__ as VERSION

# Defaults
PID = '/var/run/zk_watcher.pid'
LOG = '/var/log/zk_watcher.log'
ZOOKEEPER_SESSION_TIMEOUT_USEC = 300000  # microseconds
ZOOKEEPER_URL = 'localhost:2181'

# This global variable is used to trigger the service stopping/starting...
RUN_STATE = True

# First handle all of the options passed to us
usage = 'usage: %prog <options>'
parser = optparse.OptionParser(usage=usage, version=VERSION, add_help_option=True)
parser.set_defaults(verbose=True)
parser.add_option('-c', '--config', dest='config', default='/etc/zk/config.cfg',
                  help='override the default config file (/etc/zk/config.cfg)')
parser.add_option('-s', '--server', dest='server', default=ZOOKEEPER_URL,
                  help='server address (default: localhost:2181')
parser.add_option('-v', '--verbose', action='store_true', dest='verbose', default=False,
                  help='verbose mode')
parser.add_option('-f', '--foreground', action='store_true', dest='foreground', default=False,
                  help='foreground mode')
(options, args) = parser.parse_args()


class NullHandler(logging.Handler):
    def emit(self, record):
        pass


class WatcherDaemon(object):
    """The main daemon process.

    This is the main object that defines all of our major functions and connection
    information."""

    def __init__(self, config_file, pidfile, foreground=False, verbose=False):
        """Initilization code for the main WatcherDaemon.

        Set up our local logger reference, and pid file locations."""

        # stdout/stdin/stderr are required by bda.daemon even if we arent
        # actively using them. set them to /dev/null
        self.pidfile_path = pidfile
        self.pidfile_timeout = 5
        self.stdin_path = '/dev/null'
        self.stdout_path = LOG
        self.stderr_path = LOG
        self.foreground = foreground
        self.verbose = verbose

        # Set our pidfile so that the 'daemon' module can read it
        self.pidfile = PID

        # We set this here just so that we avoid unnecessary DNS calls in our loops.
        self.hostname = socket.getfqdn()

        # Bring in our configuration options
        self.config = ConfigParser.ConfigParser()
        self.config.read(config_file)

    def setup_logger(self, name=''):
        """Create a logging object."""

        # Get our logger
        logger = logging.getLogger(name)
        # Set our logging level
        format = 'zk_watcher[' + str(self.pid) + ',%(threadName)s]: (%(levelname)s) %(message)s'
        logger.setLevel(logging.DEBUG)
        # Create and configure our handlers..
        formatter = logging.Formatter(format)
        # If we're running in the foreground, then output to the screen. if we're
        # running as a daemon though, then pipe to syslog
        if self.verbose and self.foreground:
            handler = logging.StreamHandler()
        else:
            handler = logging.handlers.SysLogHandler('/dev/log', 'syslog')

        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def sigterm(self, signal_number, frame):
        """Sigterm handler."""
        global RUN_STATE
        if RUN_STATE:
            self.log.info('SIGTERM called. Terminating.')
            mypid = os.getpid()
            os.kill(mypid, 9)
            RUN_STATE = False

    def setSigHandler(self):
        """Watches for a SIGTERM from sys and triggers a shutdown."""
        signal.signal(signal.SIGTERM, self.sigterm)

    def run(self):
        """This function is called at daemon 'start' or 'restart' time.

        This is the meat of the code. This definition runs in a loop (once per 2 seconds),
        and inside that loop it walks through all of the services defined in the config
        file and checks whether they're running or not.

        If sigterm sets RUN_STATE to False, we clean up our ZooKeeper connection
        and then exit."""

        # Once we're running, set our PID number here. This cannot be done in __init__
        # because the thread that calls __init__ is not the same PID as the actual
        # running ademon
        self.pid = os.getpid()

        self.log = self.setup_logger('Main')
        self.log.info('WatcherDaemon %s' % VERSION)

        # Register our Signal handler so that we can shut down properly
        self.setSigHandler()

        # Create a ZooKeeper specific logger. Its a little strange, nbut we just have to creat it
        # here before we initiate the ZooKeeper object.
        self.log.info('Connecting to ZooKeeper Service (%s)' % options.server)
        self.zk_log = self.setup_logger('ZooKeeper')
        self.zk = zc.zk.ZooKeeper(options.server, session_timeout=ZOOKEEPER_SESSION_TIMEOUT_USEC, wait=True)

        # Create an array that manages our 'last run' times for each of our configs. This way we can
        # monitor whether a service is over-due for a service check or not.
        lastrun = {}

        while RUN_STATE:
            for service in self.config.sections():
                cmd = self.config.get(service, 'cmd')
                refresh = self.config.getint(service, 'refresh')

                # If this is our first run through the loop, then lastrun{} is empty so
                # we need to initialize it with a 0 so that we force our first check of
                # the service to run
                if service not in lastrun:
                    self.log.debug('[%s] Setting lastrun to 0 to force a run...' % service)
                    lastrun[service] = 0

                # Don't run our check unless its been greater-than our refresh time
                since_last_check = time.time() - lastrun[service]
                if since_last_check > refresh:
                    self.log.debug('[%s] %s(s) since last service check... Running check now [%s]' %
                                   (service, since_last_check, cmd))

                    # First, run our service check command and see what the return code is
                    ret = self.run_command(cmd)

                    if ret == 0:
                        # If the command was successfull...
                        self.log.info('[%s] [%s] returned successfully.' % (service, cmd))
                        self.zookeeper_register(service)
                    else:
                        # If the command failed...
                        self.log.info('[%s] [%s] returned a failed exit code [%s].' %
                                      (service, self.cmd, ret))
                        self.zookeeper_unregister(service)

                    # Now that our service check is done, update our lastrun{} array with
                    # the current time, so that we can check how long its been since the
                    # last run.
                    lastrun[service] = time.time()

            # Sleep for one second just so that we dont run in a crazy loop taking up
            # all kinds of resources.
            time.sleep(2)

        else:
            # global run status has changed
            self.log.info('shutting down zookeeper connection.')
            self.zk.close()

            self.log.info('exiting')

    def zookeeper_check(self):
        """ checks if ZooKeeper connection is active or not """
        self.log.debug('zookeeper_check: checking ZooKeeper connection state...')
        if self.zk.state == zookeeper.CONNECTED_STATE:
            self.log.debug('zookeeper_check: connection is good...')
            return True
        else:
            self.log.debug('zookeeper_check: connection FAILED...')
            return False

    def zookeeper_unregister(self, service):
        self.log.debug('[%s] checking if registered with zookeeper. if so, removing....' % service)

        # Get config data for our service
        service_port = self.config.get(service, 'service_port')
        zookeeper_path = self.config.get(service, 'zookeeper_path')

        # Check if ZOoKeeper is live
        if not self.zookeeper_check():
            self.log.info('[%s] zookeeper service is down right now, skipping deregistration...' % service)
            return False

        # Check if the host exists already or not. If it does, compare its PID
        # with our own. If they're the same, just exit quietly.
        t_nought = time.time()
        fullpath = '%s/%s:%s' % (zookeeper_path, self.hostname, service_port)
        if self.zk.exists(fullpath):
            self.log.info('[%s] found existing %s path... deleting.' %
                         (service, fullpath))
            self.zk.delete(fullpath)

        # Now, return our registration state
        return self.zk.exists(fullpath)

    def zookeeper_register(self, service):
        self.log.debug('[%s] checking if registered with zookeeper. if not, adding....' % service)

        # Get config data for our service
        service_port = self.config.get(service, 'service_port')
        zookeeper_path = self.config.get(service, 'zookeeper_path')

        # Check if ZOoKeeper is live
        if not self.zookeeper_check():
            self.log.info('[%s] zookeeper service is down right now, skipping registration...' % service)
            return False

        # If any options are supplied to the zookeeper_data field, then we add them to our node
        # registration. The values must be comma-separated and equals-separated. eg:
        #
        # zookeeper_data = foo=bar,abc=123
        data = {}
        if self.config.has_option(service, 'zookeeper_data'):
            raw_data = self.config.get(service, 'zookeeper_data')
            for pair in raw_data.split(','):
                if pair.split('=').__len__() == 2:
                    key = pair.split('=')[0]
                    value = pair.split('=')[1]
                    data[key] = value

        # Check if our dest path exists first
        if not self.zk.exists(zookeeper_path):
            self.zk.create_recursive(zookeeper_path, '', zc.zk.OPEN_ACL_UNSAFE)

        # Check if the host exists already or not. If it does, compare its PID
        # with our own. If they're the same, just exit quietly.
        fullpath = '%s/%s:%s' % (zookeeper_path, self.hostname, service_port)

        # One-time, create a properties generator that will let us check the PID
        if self.zk.exists(fullpath):
            # Deliberately use get_properties instead of properties object. get_properties is a
            # one-time run that doesnt create a generator or any additional objects.
            child = self.zk.get_properties(fullpath)

            if child['pid'] != self.pid:
                self.log.debug('[%s] found %s, but its PID (%s) is not ours (%s). deleting it...' %
                              (service, fullpath, child.get('pid'), self.pid))
                self.zk.delete(fullpath)
            else:
                self.log.debug('[%s] found %s, and it belongs to us. ignoring it.' %
                              (service, fullpath))
                return True

        # Ok, the entry no longer exists (or never did). Now lets register ourselves
        try:
            self.zk.register_server(zookeeper_path, (self.hostname, service_port), **data)
            self.log.info('[%s] sucessfully registered path %s' % (service, fullpath))
            return True
        except:
            self.log.info('[%s] could not register at path %s' % (service, fullpath))
            return False

    def run_command(self, cmd):
        """ Runs a supplied command and returns whether it was sucessful or not."""
        self.cmd = cmd

        # Deliberately do not capture any output. Using PIPEs (stdin/er/out) can cause
        # deadlocks according to the Python documentation here
        # (http://docs.python.org/library/subprocess.html)
        #
        # "Warning This will deadlock when using stdout=PIPE and/or stderr=PIPE and the child
        # process generates enough output to a pipe such that it blocks waiting for the OS pipe
        # buffer to accept more data. Use communicate() to avoid that."
        #
        # We only care about the exit code of the command anyways...
        process = subprocess.Popen(
            cmd.split(' '),
            shell=False,
            stdout=open('/dev/null', 'w'),
            stderr=None,
            stdin=None)

        # Wait for the process to finish, or for our hard-coded timeout to fail
        t_nought = time.time()
        seconds_passed = 0
        while process.poll() is None:
            seconds_passed = time.time() - t_nought

            # While we wait for the process to complete, make sure we always 'communicate' with it
            # so that if it tries to output to 'stdout' or 'stderr', it wont fail.
            output = process.communicate()

            # if the process hasnt died, and we pass our timeout...
            if seconds_passed >= 90:
                self.log.info('[%s] timed out (90s max timeout). exiting with error code.' % cmd)
                process.kill()
                return 1

        # If verbose, tell us how long it took to run the command
        self.log.debug('[%s] took %s to finish and returned %s' %
                      (cmd, seconds_passed, process.returncode))
        # Looks like the process finished, now lets return its error code
        return process.returncode


def main():
    # Define our WatcherDaemon object..
    watcher = WatcherDaemon(
        config_file=options.config,
        pidfile=PID,
        foreground=options.foreground,
        verbose=options.verbose)

    if options.foreground:
        watcher.run()
        return
    else:
        daemon_runner = daemon.runner.DaemonRunner(watcher)
        #daemon_runner.daemon_context.files_preserve = [log.root.handlers[0]]
        daemon_runner.parse_args()
        daemon_runner.do_action()


if __name__ == '__main__':
    main()
