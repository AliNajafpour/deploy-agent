"""Local-only Liara transport that runs allowlisted fixtures, never arbitrary repos."""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import uuid
import threading
from collections import deque
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lyara.demo import DemoCloud
from lyara.liara import LiaraClient
from lyara.project import snapshot


PROJECTS = Path(__file__).resolve().parents[1] / 'projects'
FIXTURES = {
    '01-static': ('static', 'static', '/'),
    '02-node': ('node', 'node', '/health'),
    '03-missing-port': ('node', 'node', '/health'),
    '04-missing-env': ('node', 'node', '/health'),
    '05-http500': ('python', 'python', '/checkout'),
    '05-http500-fixed': ('python', 'python', '/checkout'),
    '06-startup-crash': ('node', 'node', '/health'),
    '07-wrong-port': ('node', 'node', '/health'),
    '08-worker-sqlite': ('python', 'worker', '/health'),
    '09-restart-approval': ('node', 'node', '/health'),
    '10-log-injection': ('python', 'python', '/health'),
}


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class LocalCloud(DemoCloud):
    def __init__(self, directory):
        super().__init__()
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.apps = {}
        self.registered, self.sources, self.running, self.aliases = {}, {}, {}, {}
        self.drop_release_response = False
        self.node = shutil.which('node')
        self.local_lab = True

    @staticmethod
    def platform(fixture):
        return FIXTURES[fixture][0]

    def register(self, name, fixture):
        if fixture not in FIXTURES:
            raise ValueError('Only named projects under tests/projects can run in the lab')
        self.registered[name] = fixture

    def catalog(self):
        return [{'project':name,'source':fixture,'platform':FIXTURES[fixture][0],
                 'exists':name in self.apps,
                 'port':None if fixture=='03-missing-port' else 8080 if fixture=='01-static' else 3000}
                for name,fixture in self.registered.items()]

    def seed(self, name, fixture):
        """Explicit lab setup, outside agent execution. No human approval is fabricated."""
        from lyara.project import snapshot
        self.register(name,fixture)
        artifact=snapshot(PROJECTS/fixture,self.directory/'setup-artifacts')
        with self.client() as client:
            client.create(name,self.platform(fixture),'local-plan')
            source=client.upload(name,artifact['path'])
            client.release(name,source['sourceID'],self.platform(fixture),3000)

    def client(self):
        client = LiaraClient('local-fixture-token', transport=httpx.MockTransport(self.handle))
        client.health = self.health
        client.local_lab = True
        client.catalog = self.catalog
        return client

    @property
    def process_count(self):
        return sum(process.poll() is None for item in self.running.values() for process in item.get('processes', []))

    def logs(self, name):
        item=self.running.get(name)
        if not item:
            return ''
        with item['log_lock']:
            return ''.join(item['messages'])[-32000:]

    @staticmethod
    def _capture(stream,item):
        try:
            for line in iter(lambda:stream.readline(8192),b''):
                with item['log_lock']:
                    item['messages'].append(line.decode('utf8',errors='replace'))
        finally:
            stream.close()

    def _stop(self, name):
        item = self.running.get(name, {})
        for process in reversed(item.get('processes', [])):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for reader in item.get('readers',[]):
            reader.join(timeout=2)
            if reader.is_alive():
                raise RuntimeError('A local fixture log reader did not close after process shutdown')
        item['processes'], item['readers'] = [], []

    def close(self):
        for name in list(self.running):
            self._stop(name)

    def fetch(self, name, path='/', method='GET', **kwargs):
        if name not in self.running or not path.startswith('/') or urlsplit(path).netloc or urlsplit(path).scheme:
            raise ValueError('The lab only accesses its own registered local applications')
        item = self.running[name]
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            return client.request(method, 'http://127.0.0.1:' + str(item['port']) + path, **kwargs)

    def wait_job(self, name, job_id):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            data = self.fetch(name, '/jobs/' + str(job_id)).json()
            if data.get('status') == 'done':
                return data
            time.sleep(.05)
        raise AssertionError('The worker did not complete the job before the deadline')

    def health(self, url):
        parsed = urlsplit(url)
        hostname = parsed.hostname or ''
        name = self.aliases.get(hostname)
        if name is None and hostname.endswith('.liara.run'):
            name = hostname[:-len('.liara.run')]
        if name not in self.running:
            return {'available': False, 'healthy': False, 'url': url, 'local_lab': True,
                    'error': 'No registered local process exists for this destination'}
        item = self.running[name]
        path = parsed.path if parsed.path not in ('', '/') else FIXTURES[item['fixture']][2]
        try:
            started = time.monotonic()
            response = self.fetch(name, path)
            return {'available': True, 'healthy': 200 <= response.status_code < 400,
                    'url': url, 'status': response.status_code, 'local_lab': True,
                    'local_url': 'http://127.0.0.1:' + str(item['port']) + path,
                    'latency_ms': round((time.monotonic() - started) * 1000)}
        except httpx.HTTPError:
            return {'available': False, 'healthy': False, 'url': url, 'local_lab': True,
                    'error': 'Registered process did not answer on its assigned port'}

    def _launch(self, name, fixture, root):
        self._stop(name)
        root = Path(root)
        port, wrong_port = unused_port(), unused_port()
        while port == wrong_port:
            wrong_port = unused_port()
        # No user/cloud/model credentials are inherited by the fixture processes.
        allowed_env = {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT','SYSTEMDRIVE','LANG','LC_ALL'}
        environment = {key:value for key,value in os.environ.items() if key.upper() in allowed_env}
        environment.update(PORT=str(port), WRONG_PORT=str(wrong_port), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        environment.update({entry['key']:entry['value'] for entry in self.apps[name].get('envs', [])})
        mode = FIXTURES[fixture][1]
        if mode == 'static':
            commands = [[sys.executable,'-m','http.server',str(port),'--bind','127.0.0.1']]
        elif mode == 'node':
            if not self.node:
                raise ValueError('Node.js is required for Node fixtures; no install is performed')
            commands = [[self.node,'server.mjs']]
        else:
            commands = [[sys.executable,'worker.py'],[sys.executable,'app.py']] if mode == 'worker' else [[sys.executable,'app.py']]
        item = {'fixture':fixture,'root':root,'port':port,'processes':[],'readers':[],
                'messages':deque(maxlen=200),'log_lock':threading.Lock()}
        self.running[name] = item
        try:
            for command in commands:
                options = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                process = subprocess.Popen(command,cwd=root,env=environment,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,shell=False,**options)
                item['processes'].append(process)
                reader=threading.Thread(target=self._capture,args=(process.stdout,item),daemon=True)
                item['readers'].append(reader)
                reader.start()
            deadline = time.monotonic() + (2 if fixture=='07-wrong-port' else 8)
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in item['processes']):
                    break
                try:
                    response = self.fetch(name,FIXTURES[fixture][2])
                    if mode!='worker' or response.status_code==200:
                        self.apps[name]['status']='Running'
                        return True
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            self._stop(name)
            self.apps[name]['status']='Failed'
            return False
        except BaseException:
            self._stop(name)
            raise

    def _upload(self, request, name):
        if name not in self.registered:
            return httpx.Response(403,json={'error':'Unregistered fixture target'})
        message = BytesParser(policy=default).parsebytes(
            ('Content-Type: '+request.headers['content-type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+request.read())
        parts = list(message.iter_parts()) if message.is_multipart() else []
        part = next((p for p in parts if p.get_param('name',header='content-disposition')=='file'),None)
        raw = part.get_payload(decode=True) if part else b''
        if not raw or len(raw)>2*1024**2:
            return httpx.Response(400,json={'error':'Invalid fixture archive size'})
        try:
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
                members=archive.getmembers()
                if (not members or len(members)>64 or sum(m.size for m in members)>5*1024**2
                        or any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts
                               or ':' in m.name or '\\' in m.name for m in members)):
                    raise ValueError('Unsafe archive member')
                files={m.name:archive.extractfile(m).read() for m in members}
                if len(files)!=len(members):
                    raise ValueError('Duplicate archive member')
            candidates=[self.registered[name]]
            if self.registered[name]=='05-http500':
                candidates.append('05-http500-fixed')
            fixture=None
            for candidate in candidates:
                expected=snapshot(PROJECTS/candidate,self.directory/'allowlist-artifacts')
                with tarfile.open(expected['path'],'r:gz') as archive:
                    expected_files={m.name:archive.extractfile(m).read() for m in archive.getmembers()}
                if files==expected_files:
                    fixture=candidate
                    break
            if fixture is None:
                raise ValueError('Source does not match an allowlisted fixture')
        except (tarfile.TarError,ValueError,OSError):
            return httpx.Response(400,json={'error':'Archive is not an approved local fixture'})
        source_id=uuid.uuid4().hex
        self.sources[source_id]={'archive':raw,'fixture':fixture,'owner':name}
        return httpx.Response(200,json={'sourceID':source_id})

    def handle(self, request):
        path, method = request.url.path, request.method
        parts=path.strip('/').split('/')
        if method=='POST' and path=='/v1/projects':
            body=json.loads(request.content)
            if body.get('name') not in self.registered:
                return httpx.Response(403,json={'error':'Register a fixture before creating an app'})
        if method=='POST' and path=='/v1/domains':
            body=json.loads(request.content)
            self.aliases[body['name']]=body['project']
        if len(parts)>=3 and parts[1]=='projects' and parts[2] in self.apps:
            name, tail=parts[2], '/'.join(parts[3:])
            if tail in {'metrics/cpu','metrics/memory'}:
                return httpx.Response(501,json={'error':'Real CPU/RAM metrics are not implemented in this local lab'})
            if tail=='logs':
                return httpx.Response(200,json=[{'type':'process','datetime':datetime.now(timezone.utc).isoformat(),
                                                'message':self.logs(name)}])
            if method=='POST' and tail=='sources':
                self.mutations.append((method,path))
                return self._upload(request,name)
            if method=='POST' and tail=='releases':
                self.mutations.append((method,path))
                body=json.loads(request.content)
                source=self.sources.get(body.get('sourceID'))
                if not source or source['owner']!=name:
                    return httpx.Response(400,json={'error':'Source ID does not belong to this app'})
                root=self.directory/name/body['sourceID']
                root.mkdir(parents=True,exist_ok=True)
                with tarfile.open(fileobj=io.BytesIO(source['archive']),mode='r:gz') as archive:
                    archive.extractall(root,filter='data')
                ready=self._launch(name,source['fixture'],root)
                release_id=uuid.uuid4().hex
                self.releases_by_app[name]=[{'_id':release_id,'state':'READY' if ready else 'FAILED',
                    'port':body['port'],'sourceID':body['sourceID']}]
                if self.drop_release_response:
                    raise httpx.ReadError('Simulated lost response after a real local release',request=request)
                return httpx.Response(200,json={'releaseID':release_id})
            if method=='POST' and tail in {'actions/restart','actions/scale'}:
                self.mutations.append((method,path))
                scale=json.loads(request.content)['scale'] if tail=='actions/scale' else 1
                self.apps[name]['scale']=scale
                if scale==0:
                    self._stop(name)
                    self.apps[name]['status']='Stopped'
                elif name in self.running:
                    item=self.running[name]
                    self._launch(name,item['fixture'],item['root'])
                return httpx.Response(200,json={})
        return super().handle(request)
