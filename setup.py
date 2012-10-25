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

import os
import shutil
import subprocess

from distutils.command.clean import clean
from distutils.command.sdist import sdist
from setuptools import setup

PACKAGE = 'zk_watcher'
__version__ = None
execfile(os.path.join(PACKAGE, 'version.py'))  # set __version__


class SourceDistHook(sdist):

    def run(self):
        with open('version.rst', 'w') as f:
            f.write(':Version: %s\n' % __version__)
        shutil.copy('README.rst', 'README')
        subprocess.call(['rst2man', 'zk_watcher.rst', 'zk_watcher.1'])
        sdist.run(self)
        os.unlink('MANIFEST')
        os.unlink('README')
        os.unlink('zk_watcher.1')
        os.unlink('version.rst')


class CleanHook(clean):

    def run(self):
        clean.run(self)

        def maybe_rm(path):
            if os.path.exists(path):
                shutil.rmtree(path)
        if self.all:
            maybe_rm('zk_watcher.egg-info')
            maybe_rm('dist')


setup(
    name='zk_watcher',
    version=__version__,
    description='Python-based service registration daemon for Apache ZooKeeper',
    long_description=open('README.rst').read(),
    author='Matt Wise',
    author_email='matt@nextdoor.com',
    url='https://github.com/Nextdoor/zkwatcher',
    download_url='http://pypi.python.org/pypi/zk_watcher#downloads',
    license='Apache License, Version 2.0',
    keywords='zookeeper apache zk',
    packages=[PACKAGE],
    entry_points={
        'console_scripts': ['zk_watcher = zk_watcher.zk_watcher:main'],
    },
    data_files=[
        ('man/man1', ['zk_watcher.1']),
        ('/etc/zk', ['extras/zk/config.cfg']),
    ],
    install_requires=[
        'zc.zk',
        'zc-zookeeper-static',
        'python-daemon',
        'setuptools',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Topic :: Software Development',
        'License :: OSI Approved :: Apache Software License',
        'Intended Audience :: Developers',
        'Programming Language :: Python',
        'Operating System :: POSIX',
        'Natural Language :: English',
    ],
    cmdclass={'sdist': SourceDistHook, 'clean': CleanHook},
)
