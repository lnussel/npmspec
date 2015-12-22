#!/usr/bin/python
# Copyright (c) 2015 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from pprint import pprint
import os, sys, re
import logging
import cmdln
import requests
import json
from jinja2 import Environment, FileSystemLoader
from xml.etree import cElementTree as ET
import osc.conf, osc.core
import datetime

# very simple spec file parser
class Spec(object):
    def __init__(self, fn = None):
        self.lines = []
        if fn is not None:
            self.lines = self._read(fn)

    def _read(self, fn):
        with open(fn, 'r') as fh:
            return [l[:-1] for l in fh.readlines()]

    def settag(self, tag, value):
        tag = tag.capitalize()
        for i, line in enumerate(self.lines):
            if line.startswith('%package ') or line.startswith('%description'):
                break
            if line.startswith(tag+':'):
                spaces = re.match(r'\s*', line[len(tag)+1:]).group()
                self.lines[i] = "{}:{}{}".format(tag, spaces, value)

    def gettag(self, tag):
        tag = tag.capitalize()
        for line in self.lines:
            if line.startswith('%package ') or line.startswith('%description'):
                break
            if line.startswith(tag+':'):
                return line[len(tag)+1:].lstrip()
        return None

    def __str__(self):
        return '\n'.join(self.lines)


class BoilderPlate(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

        self.templates = Environment(loader = FileSystemLoader(os.path.dirname(__file__)))

        osc.conf.get_config()

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        return parser

    def postoptparse(self):
        level = None
        if self.options.debug:
            level  = logging.DEBUG
        elif self.options.verbose:
            level = logging.INFO

        logging.basicConfig(level = level)

        self.logger = logging.getLogger(self.optparser.prog)

    @cmdln.option("-f", "--force", action="store_true",
                  help="force something")
    def do_genspec(self, subcmd, opts, pkg):
        """${cmd_name}: foo bar

        ${cmd_usage}
        ${cmd_option_list}
        """

        print self.genspec(self.get_templatedata(self.fetch_registry(pkg)))

    @cmdln.option("-u", "--update", action="store_true",
                  help="update if exists")
    @cmdln.option("-f", "--force", action="store_true",
                  help="force something")
    def do_genpkg(self, subcmd, opts, name):
        """${cmd_name}: foo bar

        ${cmd_usage}
        ${cmd_option_list}
        """

        exists = False
        pkg = 'nodejs-{}'.format(name)
        dst = pkg
        specfn = pkg+'.spec'
        if os.path.exists(specfn):
            dst = '.'
            exists = True
        elif os.path.exists(os.path.join(dst, specfn)):
            exists = True

        specfn = os.path.join(dst, specfn)

        data = self.fetch_registry(name)

        if not exists:
            v = self.check_dln_exists('nodejs-{}'.format(name))
            if v is not None:
                self.logger.warn("Note: nodejs-{} exists in obs".format(name))
                self.logger.warn("current version: {}, obs version: {}".format(data['version'], v))
                return

        if not exists and dst != '.' and not os.path.exists(dst):
            if os.path.exists('.osc'):
                osc.core.createPackageDir(dst)
            else:
                os.mkdir(dst)

        if exists:
            spec = Spec(specfn)
            oldver = spec.gettag('Version')
            if oldver is None:
                raise Exception("old version not defined?")
            if oldver == data['version']:
                self.logger.info("same version exists")
                return
            if 'dependencies' in data:
                print data['dependencies']
            if opts.update:
                spec.settag('Version', data['version'])
                with open(specfn, 'w') as fh:
                    fh.write(str(spec))
                self.write_changes_file(dst, pkg, data)
                self.download(data['dist']['tarball'], dst)
        else:
            context = self.get_templatedata(data)
            with open(specfn, 'w') as fh:
                fh.write(self.genspec(context))

            with open("{}/_service".format(dst), 'w') as fh:
                fh.write(self.genservice(context))

            self.write_changes_file(dst, pkg, data)
            self.download(data['dist']['tarball'], dst)

    def write_changes_file(self, dst, pkg, data):
        lines = []
        fn = os.path.join(dst, "{}.changes".format(pkg))
        if os.path.exists(fn):
            with open(fn, 'r') as fh:
                lines = fh.readlines()
        with open(fn+'.new', 'w') as fh:
            author = 'lnussel@suse.de' # FIXME
            fh.write('-' * 67 + '\n')
            fh.write("%s - %s\n" % (
                datetime.datetime.utcnow().strftime('%a %b %d %H:%M:%S UTC %Y'),
                author))
            fh.write('\n')
            fh.write("- Update to version %s:\n" % data['version'])
            fh.write('\n')
            fh.write(''.join(lines))
        os.rename(fn+'.new', fn)

    def check_dln_exists(self, name):
        apiurl = 'https://build.opensuse.org'
        
        self.logger.debug("checking devel:languages:nodejs")
        r = requests.get(apiurl + '/source/devel:languages:nodejs/{}'.format(name))
        if r.status_code == requests.codes.ok:
            r = requests.get(apiurl +
                    '/build/devel:languages:nodejs/Tumbleweed/x86_64/_repository/{}?view=fileinfo'.format(name),
                    stream = True)
            if r.status_code == requests.codes.ok:
                xml = ET.parse(r.raw)
                xml = xml.getroot()
                v = xml.find('version')
                if v is not None:
                    return v.text
            return ''

        return None

    def download(self, url, dst):

        fn = os.path.join(dst, os.path.basename(url))
        if os.path.exists(fn):
            return

        r = requests.get(url, stream=True)
        r.raise_for_status()
        with open(fn, 'w') as fh:
            for chunk in r.iter_content(4096):
                fh.write(chunk)

    def fetch_registry(self, pkg):
        data = None

        fn = os.path.expanduser('~/.npm/registry.npmjs.org/{}/.cache.json'.format(pkg))
        if os.path.exists(fn):
            with open(fn, 'r') as fh:
                data = json.load(fh)

        headers = {
                'accept-encoding' : 'gzip',
                'accept' : 'application/json',
                }
        if data is not None:
            headers['etag'] = data['_etag']
            headers['if-none-match'] = data['_etag']
        url = "https://registry.npmjs.org/{}".format(pkg)
        r = requests.get(url, headers = headers)
        if data is None or r.status_code != 304:
            r.raise_for_status()
            data = r.json()
            data['_etag'] = r.headers['etag']
            if not os.path.exists(os.path.dirname(fn)):
                os.makedirs(os.path.dirname(fn))
            with open(fn, 'w') as fh:
                fh.write(json.dumps(data))
        else:
            self.logger.debug("using cached data")

        version = data['dist-tags']['latest']
        return data['versions'][version]

    def get_templatedata(self, data):
        context = {
            'name' : data['name'],
            'version' : data['version'],
            'source' : data['dist']['tarball'],
            'description' : data['description'],
            'license' : data['license'] if 'license' in data else 'FIXME',
            'url' : data['homepage'],
            'summary' : data['description'].split('\n')[0],
        }

        requires = []
        if 'dependencies' in data:
            for (k, v) in data['dependencies'].items():
                if v[0] == '=':
                    v = v[1:]
                if v[0] == 'v':
                    v = v[1:]
                if v[0] == '^' or v[0] == '~':
                    v = v[1:]
                    a = v.split('.')
                    requires.append("{} >= {}".format(k, v))
                    #let's keep it simple for now
                    #requires.append("{} < {}.{}.0".format(k, a[0], int(a[1])+1))
                else:
                    raise Exception("unsupported version specification {}".format(v[0]))
        context['requires'] = requires
        print requires

        return context

    def genspec(self, context):

        t = self.templates.get_template('spec.template')
        
        return t.render(context)

    def genservice(self, context):

        t = self.templates.get_template('service.template')
        
        return t.render(context)


if __name__ == "__main__":
    app = BoilderPlate()
    sys.exit( app.main() )

# vim: sw=4 et
