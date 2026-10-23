import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from lyara.demo import DemoCloud
from lyara.service import Assistant
from lyara.web import create_app


class WebV2Checks(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.cloud=DemoCloud();self.assistant=Assistant(self.root,self.root/'state',self.cloud.client(),None)
        self.app=create_app(self.assistant,'web-private',{'testserver'})
        self.web=TestClient(self.app);self.headers={'Authorization':'Bearer web-private'}

    def tearDown(self):
        self.app.state.monitoring.close();self.web.close();self.assistant.close();self.temporary.cleanup()

    def test_public_mode_needs_no_code_but_retains_origin_and_generation_checks(self):
        public=create_app(self.assistant,'',{'testserver'})
        try:
            with TestClient(public) as client:
                response=client.get('/api/controls')
                self.assertEqual(response.status_code,200)
                generation=response.json()['generation']
                self.assertEqual(client.put('/api/controls',headers={'Origin':'https://outside.example'},
                    json={'enabled':True,'expected_generation':generation}).status_code,403)
                self.assertEqual(client.put('/api/controls',json={'enabled':True,'expected_generation':generation}).status_code,200)
                self.assertEqual(client.put('/api/controls',json={'enabled':False,'expected_generation':generation}).status_code,403)
            self.assertEqual(self.cloud.mutations,[])
        finally:public.state.monitoring.close()

    def test_new_controls_and_handoffs_are_authenticated_and_same_origin(self):
        with patch.object(self.assistant,'controls',create=True,return_value={'stopped':False,'generation':0,'managed_projects':[]}) as controls:
            for path in ['/api/controls','/api/hosting','/api/runs/unknown/handoff']:
                self.assertEqual(self.web.get(path).status_code,401)
            self.assertEqual(self.web.get('/api/controls',headers=self.headers).status_code,200)
            response=self.web.put('/api/controls',headers={**self.headers,'Origin':'https://attacker.example'},json={'enabled':True,'expected_generation':0})
            self.assertEqual(response.status_code,403)
            self.assertEqual(controls.call_count,1)
        with patch.object(self.assistant,'emergency',create=True) as emergency:
            for body in [{'enabled':True},{'enabled':True,'expected_generation':-1},{'enabled':True,'expected_generation':0,'extra':'secret'},{'enabled':'true','expected_generation':0},{'enabled':True,'expected_generation':True}]:
                self.assertEqual(self.web.put('/api/controls',headers=self.headers,json=body).status_code,422)
            emergency.assert_not_called()
        self.assertEqual(self.cloud.mutations,[])

    def test_emergency_is_not_queued_behind_a_busy_run(self):
        started=threading.Event();release=threading.Event()
        def work(_):started.set();release.wait(5)
        result={'stopped':True,'generation':1,'managed_projects':[]}
        try:
            with patch.object(self.assistant,'new',return_value={'id':'busy'}),patch.object(self.assistant,'process',side_effect=work),patch.object(self.assistant,'emergency',create=True,return_value=result) as stop:
                self.assertEqual(self.web.post('/api/runs',headers=self.headers,json={'message':'inspect','project':'site-one','intent':'diagnose'}).status_code,202)
                self.assertTrue(started.wait(2))
                response=self.web.put('/api/controls',headers=self.headers,json={'enabled':True,'expected_generation':0})
                self.assertEqual(response.json(),result)
                self.assertFalse(release.is_set(),'emergency completes while run worker remains occupied')
                stop.assert_called_once_with(True,0)
        finally:release.set()

    def test_managed_scope_is_an_explicit_versioned_request(self):
        result={'stopped':False,'generation':3,'managed_projects':['site-one']}
        with patch.object(self.assistant,'manage',create=True,return_value=result) as manage:
            response=self.web.put('/api/managed/site-one',headers=self.headers,json={'enabled':True,'expected_generation':2})
            self.assertEqual(response.json(),result);manage.assert_called_once_with('site-one',True,2)
            self.assertEqual(self.web.put('/api/managed/bad.name',headers=self.headers,json={'enabled':True,'expected_generation':3}).status_code,400)
        with patch.object(self.assistant,'manage',create=True,side_effect=PermissionError('Access changed')):
            response=self.web.put('/api/managed/site-one',headers=self.headers,json={'enabled':True,'expected_generation':2})
            self.assertEqual(response.status_code,403)
        self.assertEqual(self.cloud.mutations,[])

    def test_github_import_uses_existing_importer_and_opaque_source(self):
        imported={'source':'upload:fixed-source','label':'site','github':{'repository':'https://github.com/owner/site','commit':'a'*40}}
        with patch('lyara.github_source.import_github',return_value=imported) as importer:
            self.assertEqual(self.web.post('/api/github',json={'url':'https://github.com/owner/site'}).status_code,401)
            response=self.web.post('/api/github',headers=self.headers,json={'url':'https://github.com/owner/site'})
            self.assertEqual(response.status_code,201);self.assertEqual(response.json(),imported)
            importer.assert_called_once_with('https://github.com/owner/site',self.assistant.data_dir,self.assistant.vault)
            self.assertEqual(self.web.post('/api/github',headers=self.headers,json={'url':'','ref':'main'}).status_code,422)
        self.assertEqual(self.cloud.mutations,[])

    def test_handoff_exports_persisted_data_and_hosting_preserves_missing_values(self):
        result={'issue':{'status':'failed','error':'missing dependency'},'source':{'sha256':'a'*64},'evidence':[]}
        catalog={'available':False,'balance':None,'options':[],'limitations':['no account access']}
        with patch.object(self.assistant,'handoff',create=True,return_value=result) as handoff,patch.object(self.assistant,'hosting_catalog',create=True,return_value=catalog):
            route='/api/runs/test-run/handoff'
            self.assertEqual(self.web.get(route+'?format=json',headers=self.headers).json(),result)
            response=self.web.get(route,headers=self.headers)
            self.assertEqual(response.status_code,200);self.assertIn('missing dependency',response.text)
            self.assertIn('attachment',response.headers['content-disposition'])
            self.assertEqual(self.web.get(route+'?format=html',headers=self.headers).status_code,400)
            self.assertEqual(handoff.call_count,2)
            self.assertEqual(self.web.get('/api/hosting',headers=self.headers).json(),catalog)
        self.assertEqual(self.cloud.mutations,[])


if __name__=='__main__':unittest.main()
