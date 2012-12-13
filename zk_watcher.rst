============
 zk_watcher
============

---------------------------------------------------------------------------------------------
`zk_watcher` is a Python script that handles registration with an `Apache ZooKeeper` service.
---------------------------------------------------------------------------------------------

.. |date| date::
.. include:: version.rst

:Author: Matt Wise <matt@nextdoor.com>
:Date: |date|
:Manual section: 1


SYNOPSIS
========

| `zk_watcher` [<start options>]
| `zk_watcher` --help


DESCRIPTION
===========

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

USAGE
=====

zk_watcher [-v|--verbose] [-c|--config=] [-s|--server=]

OPTIONS
=======

-v, --verbose
            Enables verbose logging 

-c, --config=<config file>
            Overrides the default config file location (/etc/zk/config.cfg)

-s, --server=<server:port>
            Overrides the default ZooKeeper address (localhost:2181)
