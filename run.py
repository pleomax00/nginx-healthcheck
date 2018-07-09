#! /usr/bin/env python

import urllib2
import json
import os
from string import Template
import threading
import time
import random
import copy

BASE_DIR = os.path.dirname (os.path.realpath(__file__))
CONF_DIR = os.path.join (BASE_DIR, "conf.d")

CONFIGURATION_FILES = filter (lambda x: x.endswith (".conf"), os.listdir (CONF_DIR))

sites = {}
for ifile in CONFIGURATION_FILES:
    fname, ext = os.path.splitext (ifile)
    conf = json.load (file (os.path.join (CONF_DIR, ifile)))
    sites[fname] = conf

def build_site (name, conf):
    backends = conf.get ("backends", [])
    if len (backends) == 0:
        backends = ["127.0.0.1:4534"]   # Helps nigix configuration error

    backend_str = map (lambda x: "server %s max_fails=3 fail_timeout=30s;" % (x, ), backends)

    processed_conf = {
        "configuration_name": name,
        "upstream_name": name + "_stream",
        "backends": "\n   ".join (backend_str),
    }

    sample_conf = file (os.path.join (BASE_DIR, "nginx.conf")).read ()
    res = sample_conf.format (**processed_conf)

    file (os.path.join (BASE_DIR, "backends-enabled", name + ".conf"), "w").write (res)


class Worker (threading.Thread):

    def rewrite_nginx (self, passed):
        newconf = copy.deepcopy (self.conf)
        newconf["backends"] = passed
        print "Rewrting nginx.. with", newconf
        build_site (self.name, newconf)
        print "Reloading nginx"
        os.system ("/usr/bin/sudo /etc/init.d/nginx restart")

    def check (self, backend):
        request = urllib2.Request ('http://' + backend + self.conf.get ("healthcheck"))
        try:
            response = urllib2.urlopen (request, timeout=self.conf.get ('healthcheck_timeout', 3))
        except urllib2.URLError:
            return False
        except IOError:
            return False
        code = response.getcode ()
        if code >= 200 and code < 300:
            return True
        return False

    def health_check (self):
        #print "Checking.. ", self.name, self.conf.get ("backends")
        passed = []
        for backend in self.conf.get ("backends", []):
            last_checked = self.timings.get (backend, 1)
            now = int (time.time ())
            if now - last_checked > self.conf.get ("healthcheck_interval", 15):
                res = self.check (backend)
                #print "Checked..", backend, res
                if res:
                    passed.append (backend)
                self.knownstatus[backend] = res
                self.timings[backend] = now
            else:
                if self.knownstatus.get (backend, False) == True:
                    passed.append (backend)
        print "Backends alive for {}, {}".format (self.name, passed)
        if self.laststate != tuple (passed):
            self.rewrite_nginx (passed)

        self.laststate = tuple (passed)

    def __init__ (self, name, conf):
        threading.Thread.__init__(self)
        self.name = name
        self.conf = conf
        self.timings = {}
        self.knownstatus = {}
        self.laststate = None
        self.kill_received = False

    def run (self):
        while not self.kill_received:
            time.sleep (0.2)
            self.health_check ()

th_pool = []
for name, conf in sites.items ():
    build_site (name, conf)
    th = Worker (name, conf)
    th_pool.append (th)
    th.daemon = True
    th.start ()

print "Joining.."
print th_pool
while len (th_pool) > 0:
    try:
        threads = [t.join (1) for t in th_pool if t is not None and t.isAlive()]
        #print('Threads: threads={}'.format (threads))
        if len (threads) == 0:
            break
    except KeyboardInterrupt:
        print "Ctrl-c received! Sending kill to threads..."
        for t in th_pool:
            t.kill_received = True

for th in th_pool:
    if th.isAlive ():
        th.join ()
