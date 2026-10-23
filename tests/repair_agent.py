"""Real deployment-repair checks; operator validates the candidate before execution."""
import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import venv
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx

from lyara.demo import DemoCloud
from lyara.models import Plan, RunRequest
from lyara.providers import CLIPlanner, provider_from_env
from lyara.service import Assistant
from tests.support.local_cloud import unused_port

PROJECTS = Path(__file__).parent / 'repair_projects'
WHEELS = Path(os.getenv('LYARA_WHEELHOUSE', str(Path(__file__).resolve().parents[2] / 'wallex' / 'runs' / 'portable-wheels')))


class RepairBuilder(DemoCloud):
    """Test-only builder for two fixed fixtures, never a general project executor."""
    def __init__(self, root, fixture):
        super().__init__()
        self.root, self.fixture = Path(root), fixture
        self.app = PROJECTS / fixture
        self.port = unused_port()
        self.process = None
        self.output = None
        self.log = ''
        self.sources = {}
        self.serial = 0
        self.env = {k:v for k,v in os.environ.items() if k.upper() in
                    {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','SYSTEMDRIVE'}}
        self.env.update(PORT=str(self.port), WRONG_PORT=str(unused_port()), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        self.python = None
        if fixture == '02-missing-package':
            venv.EnvBuilder(with_pip=True).create(self.root / 'venv')
            self.python = self.root / 'venv' / ('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        self.apps['demo-app']['type'] = 'python' if fixture=='02-missing-package' else 'node'
        self.baseline = self.launch(self.app, expected_healthy=False)
        self.mutations.clear()

    def close(self):
        try:
            if self.process:
                if self.process.poll() is None:
                    try:
                        with httpx.Client(timeout=2, trust_env=False) as client:
                            client.get('http://127.0.0.1:'+str(self.port)+'/__lyara_test_stop')
                        self.process.wait(timeout=5)
                    except (httpx.HTTPError, subprocess.TimeoutExpired):
                        self.process.terminate()
                        self.process.wait(timeout=5)
                self.process = None
        finally:
            if self.output:
                self.output.close()
                self.output = None

    def launch(self, source, expected_healthy):
        self.close()
        if self.fixture != '02-missing-package':
            command = [shutil.which('node'), *shlex.split(json.loads((source/'package.json').read_text())['scripts']['start'])[1:]]
        else:
            command = [str(self.python), str(source/'app.py')]
        self.output = (self.root/'process.log').open('w',encoding='utf8')
        self.process = subprocess.Popen(command,cwd=source,env=self.env,stdin=subprocess.DEVNULL,
            stdout=self.output,stderr=subprocess.STDOUT,shell=False)
        deadline = time.monotonic()+5
        healthy = False
        while time.monotonic()<deadline:
            if self.process.poll() is not None:
                break
            try:
                healthy = self.health('')['healthy']
                if healthy:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.05)
        self.output.flush()
        self.log = (self.root/'process.log').read_text(encoding='utf8')[-16000:]
        if healthy is not expected_healthy:
            raise RuntimeError('Actual fixture startup did not reach the expected health state: '+self.log)
        return {'healthy':healthy,'log':self.log}

    def health(self, url):
        try:
            with httpx.Client(timeout=.5,trust_env=False) as client:
                r=client.get('http://127.0.0.1:'+str(self.port)+'/health')
            return {'available':True,'healthy':r.status_code==200,'status':r.status_code,
                    'url':url,'local_url':'http://127.0.0.1:'+str(self.port)+'/health','local_lab':True}
        except httpx.HTTPError:
            return {'available':False,'healthy':False,'url':url,'error':'Actual local process is unavailable'}

    def client(self):
        client=super().client()
        client.health=self.health
        client.local_lab=True
        client.catalog=lambda:[{'project':'demo-app','source':self.fixture,'platform':self.apps['demo-app']['type'],'port':3000,'exists':True}]
        return client

    def inspect_archive(self, archive):
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
            members=tar.getmembers()
            files={m.name:tar.extractfile(m).read() for m in members if m.isfile()}
        # Before running anything the test operator checks the full candidate.
        expected={p.name:p.read_bytes() for p in self.app.iterdir() if p.is_file()}
        assert set(files)==set(expected), 'Unexpected files in repair candidate'
        if self.fixture=='01-start-command':
            before=json.loads(expected.pop('package.json'))
            after=json.loads(files.pop('package.json'))
            assert after.get('scripts',{}).get('start')=='node server.mjs'
            before['scripts']['start']='node server.mjs'
            assert after==before, 'Unexpected package or product changes'
        elif self.fixture=='03-wrong-port':
            before=expected.pop('server.mjs').decode('utf8')
            after=files.pop('server.mjs').decode('utf8')
            def without_plain_log(text):
                return '\n'.join(line for line in text.replace('\r\n','\n').splitlines()
                    if not re.fullmatch(r'console\.log\("[A-Za-z0-9 _;:.-]*"\);', line))
            before=without_plain_log(before.replace('process.env.WRONG_PORT','process.env.PORT'))
            after=without_plain_log(after)
            assert after in {before,before.replace('"127.0.0.1"','"0.0.0.0"')}, 'Only startup port, bind and a plain diagnostic log may change' 
        else:
            requirements=files.pop('requirements.txt').decode('utf8')
            expected.pop('requirements.txt')
            assert [s.strip() for s in requirements.splitlines() if s.strip() and not s.lstrip().startswith('#')]==['colorama==0.4.6']
        if self.fixture=='01-start-command' and files != expected:
            before=expected.pop('server.mjs').decode('utf8').replace('\r\n','\n')
            after=files.pop('server.mjs').decode('utf8').replace('\r\n','\n')
            assert after in {before, before.replace('"127.0.0.1"','"0.0.0.0"')}, 'Only startup bind host may also be corrected'
        if self.fixture=='02-missing-package' and files != expected:
            before=expected.pop('app.py').decode('utf8').replace('\r\n','\n')
            after=files.pop('app.py').decode('utf8').replace('\r\n','\n')
            assert after in {before,before.replace('HTTPServer(("127.0.0.1",','HTTPServer(("0.0.0.0",')}, 'Only startup bind may also be corrected'
        assert files==expected, 'Backend source must remain unchanged in these checks' 

    def handle(self, request):
        path=request.url.path
        if path.endswith('/logs'):
            return httpx.Response(200,json=[{'message':self.log,'type':'stderr'}])
        if request.method=='POST' and path.endswith('/sources'):
            message=BytesParser(policy=default).parsebytes(('Content-Type: '+request.headers['content-type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+request.read())
            part=next(p for p in message.iter_parts() if p.get_param('name',header='content-disposition')=='file')
            archive=part.get_payload(decode=True)
            self.inspect_archive(archive)
            self.serial+=1
            source_id='repair-source-'+str(self.serial)
            self.sources[source_id]=archive
            self.mutations.append(('POST',path))
            return httpx.Response(200,json={'sourceID':source_id})
        if request.method=='POST' and path.endswith('/releases'):
            body=json.loads(request.content)
            source=self.root/body['sourceID']
            source.mkdir()
            with tarfile.open(fileobj=io.BytesIO(self.sources[body['sourceID']]),mode='r:gz') as tar:
                tar.extractall(source,filter='data')
            if self.python:
                environment=dict(self.env)
                if WHEELS.is_dir():
                    command=[str(self.python),'-m','pip','--isolated','install','--no-index',
                        '--only-binary=:all:','--no-deps','--find-links',str(WHEELS),'-r',str(source/'requirements.txt')]
                else:
                    uv=shutil.which('uv')
                    if not uv:
                        raise RuntimeError('Set LYARA_WHEELHOUSE to local wheels or install uv with an existing offline cache.')
                    cache=Path(os.getenv('UV_CACHE_DIR') or Path(os.getenv('LOCALAPPDATA',str(Path.home()/'AppData'/'Local')))/'uv'/'cache')
                    environment['UV_CACHE_DIR']=str(cache)
                    command=[uv,'pip','install','--offline','--no-config','--no-deps',
                        '--python',str(self.python),'-r',str(source/'requirements.txt')]
                result=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=60)
                if result.returncode:
                    raise RuntimeError('Offline dependency build failed: '+result.stderr[-3000:])
                self.install_log=result.stdout+result.stderr
            self.launch(source,expected_healthy=True)
        return super().handle(request)


class ScriptedPlanner:
    name='scripted-repair-test'
    def plan(self,request,context):
        files=context['deployment_files']
        if request['source']=='03-wrong-port':
            name='server.mjs'
            content=files[name]['content'].replace('process.env.WRONG_PORT','process.env.PORT')
        else:
            name='package.json' if context['platform']=='node' else 'requirements.txt'
            content=(json.dumps({**json.loads(files[name]['content']),'scripts':{'start':'node server.mjs'}})
                 if name=='package.json' else 'colorama==0.4.6\n')
        return Plan(summary='Fix deployment blocker',steps=[{'action':'repair_deploy','title':'Repair and rebuild',
            'reason':'Startup error observed','args':{'platform':context['platform'],'port':context['port'],
                'files':[{'path':name,'before_sha256':files[name]['sha256'],'content':content}]}}])


def run(provider, report, selected=None):
    results=[]
    for fixture in ['01-start-command','02-missing-package','03-wrong-port']:
        if selected and fixture != selected:
            continue
        print('Deployment repair: '+fixture,flush=True)
        with tempfile.TemporaryDirectory(prefix='lyara-repair-check-') as temporary:
            builder=RepairBuilder(Path(temporary),fixture)
            service=Assistant(PROJECTS,Path(temporary)/'state',builder.client(),provider)
            try:
                message=('فقط خطای شروع انتشار را با شواهد اصلاح کن؛ قابلیت سایت را تغییر نده. '
                    'در این پروژهٔ آزمایشی اگر colorama جا افتاده، نسخهٔ مجاز آن 0.4.6 است.')
                proposal=service.start(RunRequest(message=message,intent='repair',project='demo-app',
                    source=fixture,platform=builder.apps['demo-app']['type'],port=3000))
                report.parent.mkdir(parents=True,exist_ok=True)
                report.with_name(report.stem+'-'+fixture+'-proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding='utf8')
                assert proposal['status']=='awaiting_approval', proposal
                assert builder.mutations==[]
                builder.inspect_archive(Path(proposal['artifact']['path']).read_bytes())
                original={p.name:p.read_bytes() for p in (PROJECTS/fixture).iterdir() if p.is_file()}
                final=service.approve(proposal['id'],proposal['plan_digest'],True)
                assert final['status']=='completed',final
                assert final['verification']['health']['healthy']
                assert final['verification']['action_checks'][0]['matched'] is True
                assert original=={p.name:p.read_bytes() for p in (PROJECTS/fixture).iterdir() if p.is_file()}
                results.append({'fixture':fixture,'passed':True,'baseline':builder.baseline,
                    'provider':provider.name,'proposal':proposal,'final':service.report(final['id']),
                    'install_log':getattr(builder,'install_log',None)})
                report.write_text(json.dumps({'cloud_api':'simulated','model':provider.name,'execution':'real local','cases':results},ensure_ascii=False,indent=2),encoding='utf8')
                print('  PASS: actual failed startup -> approved repair -> real HTTP 200',flush=True)
            except Exception as error:
                report.parent.mkdir(parents=True,exist_ok=True)
                failure={'fixture':fixture,'passed':False,'error':str(error),
                    'process_log':builder.log,'install_log':getattr(builder,'install_log',None),
                    'runs':[service.report(item['id']) for item in service.runs()]}
                report.write_text(json.dumps({'model':provider.name,'cases':[*results,failure]},ensure_ascii=False,indent=2),encoding='utf8')
                raise
            finally:
                service.close()
                builder.close()
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'cloud_api':'simulated','model':provider.name,'execution':'real local',
        'cases':results},ensure_ascii=False,indent=2),encoding='utf8')


def serve(provider, fixture, port):
    import uvicorn
    from lyara.web import create_app
    with tempfile.TemporaryDirectory(prefix='lyara-repair-ui-') as temporary:
        builder=RepairBuilder(Path(temporary),fixture)
        service=Assistant(PROJECTS,Path(temporary)/'state',builder.client(),provider)
        try:
            uvicorn.run(create_app(service),host='127.0.0.1',port=port)
        finally:
            service.close()
            builder.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=8796)
    parser.add_argument('--provider',choices=['scripted','codex','claude','api'],default='scripted')
    parser.add_argument('--fixture', choices=['01-start-command','02-missing-package','03-wrong-port'])
    parser.add_argument('--report',type=Path,default=Path('.lyara/checks/repair-execution.json'))
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    provider=(provider_from_env({**os.environ,'LYARA_PROVIDER':'api'}) if args.provider=='api'
              else ScriptedPlanner() if args.provider=='scripted' else CLIPlanner(args.provider))
    if args.serve:
        serve(provider,args.fixture or '01-start-command',args.port)
    else:
        run(provider,args.report,args.fixture)
