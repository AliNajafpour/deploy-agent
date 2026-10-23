import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from lyara.demo import DemoCloud
from lyara.models import Plan
from lyara.service import Assistant
from lyara.web import create_app


class CompletionWebChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.original='{"scripts":{"start":"node missing.mjs"}}'
        (self.root/'package.json').write_text(self.original)
        (self.root/'server.mjs').write_text('console.log("ready");')
        self.cloud=DemoCloud()
        self.service=Assistant(self.root,self.root/'state',self.cloud.client(),None)
        self.app=create_app(self.service,access_token='private-web-code',allowed_hosts={'testserver'})
        self.web=TestClient(self.app)
        self.headers={'Authorization':'Bearer private-web-code'}

    def tearDown(self):
        self.app.state.monitoring.close();self.web.close();self.service.close();self.tmp.cleanup()

    def test_monitor_controls_and_incidents_require_auth_and_do_not_mutate_cloud(self):
        for path in ['/api/monitoring','/api/incidents']:
            self.assertEqual(self.web.get(path).status_code,401)
        path='/api/monitoring/demo-app'
        self.assertEqual(self.web.put(path,json={'enabled':True}).status_code,401)
        response=self.web.put(path,headers=self.headers,json={'enabled':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['enabled'])
        self.assertEqual(self.web.get('/api/monitoring',headers=self.headers).json()[0]['project'],'demo-app')
        self.assertEqual(self.web.get('/api/incidents',headers=self.headers).json(),[])
        self.assertEqual(self.web.put(path,headers=self.headers,json={'enabled':True,'interval':1}).status_code,422)
        self.assertEqual(self.web.put(path,headers=self.headers,json={'enabled':False}).status_code,200)
        self.assertFalse(self.web.get('/api/monitoring',headers=self.headers).json()[0]['enabled'])
        self.assertEqual(self.cloud.mutations,[])

    def test_patch_is_authenticated_exact_reviewed_diff_and_does_not_publish(self):
        edit={'path':'package.json','before_sha256':hashlib.sha256(self.original.encode()).hexdigest(),
              'content':'{"scripts":{"start":"node server.mjs"}}'}
        run=self.service.start({'message':'Fix the missing entrypoint','intent':'repair','project':'demo-app','port':3000},
            Plan(summary='Correct startup',steps=[{'action':'repair_deploy','title':'Repair','reason':'Missing file',
                'args':{'platform':'node','port':3000,'files':[edit]}}]))
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        route='/api/runs/'+run['id']+'/patch'
        self.assertEqual(self.web.get(route).status_code,401)
        response=self.web.get(route,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.text,run['repair']['diff'])
        self.assertIn('attachment',response.headers['content-disposition'])
        self.assertNotIn(str(self.root),response.text)
        self.assertEqual((self.root/'package.json').read_text(),self.original)
        self.assertEqual(self.cloud.mutations,[])

    def test_doctor_runs_before_assistant_initialization_without_keys(self):
        envfile=self.root/'empty.env';envfile.write_text('')
        env={**os.environ,'LYARA_ENV_FILE':str(envfile),'LYARA_PROVIDER':'api','LYARA_LLM_API_KEY':'',
             'LIARA_API_TOKEN':'','LYARA_ACCESS_TOKEN':'','LYARA_PUBLIC_HOST':''}
        result=subprocess.run([sys.executable,'-m','lyara','doctor','--json','--production'],
            cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,1,result.stderr)
        report=json.loads(result.stdout)
        self.assertFalse(report['ready'])
        self.assertIn('liara_token',{item['id'] for item in report['checks']})
        self.assertFalse((self.root/'operations.sqlite').exists())


if __name__=='__main__':unittest.main()
