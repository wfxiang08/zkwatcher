=========
zkwatcher
=========

`zk_watcher` is a Python script that handles registration with an `Apache
ZooKeeper` service.

The goal of `zk_watcher` is to monitor a particular service on a host machine
and register that machine as a `provider` of that service at a given path
on the ZooKeeper service.

A simple example is having `zk_watcher` monitor Apache httpd by running `service
apache2 status` at a regular interval and registers with ZooKeeper at a given
path (say `/services/production/webservers`). As long as the command returns
a safe exit code (`0`), `zk_watcher` will register with ZooKeeper that this
server is providing this particular service. If the hostname of the machine
is `web1.mydomain.com`, the registration path would look like this ::

    /services/production/webservers/web1.mydomain.com:80

In the event that the service check fails, the host will be immediately de-
registered from that path.

Installation
------------

To install, run ::

    python setup.py install

or ::

    pip install zk_watcher

Service Configs
---------------

To configure, edit the '/etc/zk/config.cfg' file. The file consists of sections
that each point to a particular service you want to monitor and register with
ZooKeeper. An example file is provided, but could look like this ::

    [ssh]
    cmd: /etc/init.d/sshd status
    refresh: 60
    service_port: 22
    zookeeper_path: /services/ssh
    zookeeper_data: 

    [apache]
    cmd: /etc/init.d/apache status
    refresh: 60
    service_port: 22
    zookeeper_path: /services/web
    zookeeper_data: 

Authentication
--------------

If you wish to create a Digset authentication token and use that for your
client session with Zookeeper, you can add the settings to the config file
like this ::

    [auth]
    user: username
    password: 123456

If you do this, please look at the `ndServiceRegistry` docs to understand how
the auth token is used, and what permissions are setup by default.

Running it
----------
See the 'zk_watcher.rst' file for configuration and run-time options.

Caveats
-------
Right now you must install this package as `root`, or you must create the
`/etc/zk` directory ahead of time and change its ownership to your installation
user name. The `setup.py` uses a hard-coded path (`/etc/zk/config.cfg`) for the
config file, and will fail if it cannot create the file at that path. This will
be fixed in the next version.
