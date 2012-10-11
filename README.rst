=========
zkwatcher
=========

`zkwatcher` is a Python script that handles registration with an `Apache
ZooKeeper` service.

The goal of `zkwatcher` is to monitor a particular service on a host machine
and register that machine as a `provider` of that service at a given path
on the ZooKeeper service.

A simple example is having `zkwatcher` monitor Apache by running `service
apache2 status` at a regular interval and registers with Zookeeper at a given
path (say `/services/production/webservers`). As long as the command returns
a safe exit code (`0`), `zkwatcher` will register with Zookeeper that this
server is providing this particular service. If the hostname of the machine
is `web1.mydomain.com`, the registration path would look like this ::

    /services/production/webservers/web1.mydomain.com:80

In the event that the service check fails, the host will be immediately de-
registered from that path.

Installation
------------

To install, run ::

    pip install zkwatcher

You can also download the latest release directly from the `Python Package
Index`_ or clone the `GitHub repository`_ and install with ``python setup.py
install``.

Setup
-----
