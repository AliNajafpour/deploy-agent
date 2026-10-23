import io,json,stat,tarfile,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from lyara.demo import DemoCloud,DemoPlanner
from lyara.service import Assistant
from lyara.web import create_app


def bundle(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in files.items():z.writestr(name.replace('\\','/'),value)
    data=out.getvalue()
    # ZipInfo normalizes backslashes on Windows; preserve hostile on-disk names.
    for name in files:
        if '\\' in name:data=data.replace(name.replace('\\','/').encode(),name.encode())
    return data


class UploadChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cloud=DemoCloud()
        self.service=Assistant(self.root,self.root/'private',self.cloud.client(),DemoPlanner())
        self.client=TestClient(create_app(self.service,access_token='access',allowed_hosts={'testserver'}))
        self.headers={'Authorization':'Bearer access','Content-Type':'application/zip','X-File-Name':'my-site.zip'}
    def tearDown(self):
        self.client.close();self.service.close();self.tmp.cleanup()
    def upload(self,files):return self.client.post('/api/uploads',headers=self.headers,content=bundle(files))
    def test_zip_upload_is_authenticated_and_has_no_filesystem_path_in_response(self):
        self.assertEqual(self.client.post('/api/uploads',content=bundle({'index.html':'hi'})).status_code,401)
        r=self.upload({'site/index.html':'<h1>My site</h1>'})
        self.assertEqual(r.status_code,201,r.text)
        self.assertRegex(r.json()['source'],r'^upload:[a-f0-9]{32}$')
        self.assertNotIn(str(self.root),r.text)
        self.assertEqual(self.service.source_path(r.json()['source']).joinpath('index.html').read_text(),'<h1>My site</h1>')
    def test_uploaded_env_is_encrypted_and_applied_only_after_approval(self):
        secret='uploaded-private-example-289317'
        r=self.upload({'site/index.html':'<h1>Site</h1>','site/.env':'PAYMENT_API_KEY='+secret,'site/.env.example':'PAYMENT_API_KEY='})
        self.assertEqual(r.status_code,201,r.text)
        source=r.json()['source'];root=self.service.source_path(source)
        self.assertFalse((root/'.env').exists())
        self.assertNotIn(secret,json.dumps(self.client.get('/api/sources',headers=self.headers).json()))
        run=self.service.start({'message':'Publish my website','intent':'deploy','project':'demo-app','source':source})
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        self.assertFalse(run['context']['input_requests'])
        self.assertNotIn(secret,json.dumps(run))
        self.assertNotIn('.env',run['artifact']['files'])
        self.assertEqual(self.cloud.mutations,[])
        final=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(final['status'],'completed',final.get('error'))
        self.assertIn({'key':'PAYMENT_API_KEY','value':secret},self.cloud.apps['demo-app']['envs'])
    def test_missing_uploaded_configuration_uses_existing_typed_input_flow(self):
        r=self.upload({'index.html':'site','.env.example':'DATABASE_URL='})
        self.assertEqual(r.status_code,201,r.text)
        run=self.service.start({'message':'Publish','intent':'deploy','project':'demo-app','source':r.json()['source']})
        self.assertEqual(run['status'],'needs_input')
        self.assertEqual(run['context']['input_requests'][0]['name'],'DATABASE_URL')
        self.assertEqual(self.cloud.mutations,[])
    def test_rejects_path_escapes_windows_aliases_and_case_collisions(self):
        for name in ['../escape.txt','/absolute.txt','C:/escape.txt','folder\\escape.txt','file:ads','CON.txt','folder./index.html']:
            with self.subTest(name=name):
                self.assertEqual(self.upload({name:'no','index.html':'site'}).status_code,400)
        self.assertEqual(self.upload({'a.txt':'one','A.txt':'two'}).status_code,400)
        self.assertFalse((self.root/'escape.txt').exists())
    def test_symlinks_are_rejected(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            entry=zipfile.ZipInfo('link');entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(entry,'../../elsewhere')
        r=self.client.post('/api/uploads',headers=self.headers,content=out.getvalue())
        self.assertEqual(r.status_code,400)

    def test_executable_scripts_keep_permissions_in_deployment_on_windows_too(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            script=zipfile.ZipInfo('site/start.sh');script.create_system=3;script.external_attr=(stat.S_IFREG|0o755)<<16
            z.writestr(script,'#!/bin/sh\nnode server.js\n');z.writestr('site/index.html','Site')
        response=self.client.post('/api/uploads',headers=self.headers,content=out.getvalue())
        self.assertEqual(response.status_code,201,response.text)
        run=self.service.start({'message':'Publish','intent':'deploy','project':'demo-app','source':response.json()['source']})
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        with tarfile.open(run['artifact']['path']) as archive:self.assertEqual(archive.getmember('start.sh').mode,0o755)
    def test_invalid_empty_and_oversized_archives_leave_no_source(self):
        self.assertEqual(self.client.post('/api/uploads',headers=self.headers,content=b'not zip').status_code,400)
        self.assertEqual(self.upload({}).status_code,400)
        from lyara import uploads
        with patch.object(uploads,'MAX_FILE',4):self.assertEqual(self.upload({'index.html':'too big'}).status_code,400)
        self.assertEqual(self.client.get('/api/sources',headers=self.headers).json(),[])

    def test_corrupt_compression_cleans_every_extracted_file(self):
        data=bytearray(bundle({'.env':'API_KEY=private-test-value','index.html':'Site '*500}))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info=archive.getinfo('index.html')
            offset=info.header_offset+30+len(info.filename.encode())+len(info.extra)
        data[offset:offset+4]=b'\xff'*4
        response=self.client.post('/api/uploads',headers=self.headers,content=bytes(data))
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(list((self.service.data_dir/'uploads').iterdir()),[])

    def test_env_names_are_removed_case_insensitively(self):
        response=self.upload({'index.html':'Site','.ENV':'PAYMENT_API_KEY=private-test-value'})
        self.assertEqual(response.status_code,201,response.text)
        self.assertFalse(any(path.name.casefold()=='.env' for path in self.service.source_path(response.json()['source']).iterdir()))
    def test_sources_only_list_uploaded_projects(self):
        (self.root/'example').mkdir();(self.root/'example'/'index.html').write_text('internal example')
        r=self.upload({'index.html':'mine'})
        sources=self.client.get('/api/sources',headers=self.headers).json()
        self.assertEqual([item['source'] for item in sources],[r.json()['source']])
        self.assertEqual(sources[0]['label'],'my-site')
    def test_upload_never_runs_package_hooks_or_project_code(self):
        r=self.upload({'package.json':json.dumps({'scripts':{'postinstall':'create a file'}}),'app.py':'raise RuntimeError("must never execute")'})
        self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(self.cloud.mutations,[])
    def test_upload_ids_cannot_access_other_private_files(self):
        for name in ['upload:../inputs.key','upload:'+('a'*32)+'/../inputs.key','upload:missing']:
            with self.assertRaises(ValueError):self.service.source_path(name)
    def test_config_has_no_fixture_inventory(self):
        cfg=self.client.get('/api/config',headers=self.headers).json()
        self.assertNotIn('lab_projects',cfg)
        self.assertNotIn('local_lab',cfg)

    def test_disconnected_host_allows_upload_and_inputs_but_never_deploys(self):
        from lyara.liara import LiaraClient,LiaraError
        import httpx
        def forbidden(request):raise AssertionError('No cloud requests without a connection')
        cloud=LiaraClient('',transport=httpx.MockTransport(forbidden))
        service=Assistant(self.root,self.root/'disconnected',cloud,DemoPlanner())
        try:
            self.assertFalse(cloud.connected)
            self.assertEqual(cloud.projects(),{'projects':[]})
            with self.assertRaises(LiaraError):cloud.create('site','static','plan')
            with TestClient(create_app(service,allowed_hosts={'testserver'})) as web:
                response=web.post('/api/uploads',headers={'Content-Type':'application/zip','X-File-Name':'site.zip'},content=bundle({'index.html':'Site','.env.example':'DATABASE_URL='}))
                self.assertEqual(response.status_code,201,response.text)
                run=service.start({'message':'Publish','intent':'deploy','project':'site','source':response.json()['source']})
                self.assertEqual(run['status'],'needs_input')
                self.assertTrue(run['context']['connection_required'])
                self.assertEqual(run['plan']['steps'],[])
                self.assertEqual(run['plan']['inputs'][0]['name'],'DATABASE_URL')
                self.assertFalse(web.get('/api/config').json()['hosting_connected'])
        finally:service.close()


if __name__=='__main__':unittest.main()
