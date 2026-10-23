import io
import json
import socket
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from lyara.github_source import import_github
from lyara.inputs import InputVault
from lyara.uploads import upload_root


SHA='0123456789abcdef0123456789abcdef01234567'


def archive(files):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as output:
        for name,value in files.items():output.writestr(name,value)
    return data.getvalue()


class GitHubSourceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.vault=InputVault(self.root)
        self.requests=[]
        self.files={'repo-'+SHA+'/index.html':'Original website'}
        self.resolve=SHA
        self.commit_status=200
        self.archive_status=200
        self.location=f'https://codeload.github.com/owner/repo/legacy.zip/{SHA}'
        self.content_length=None
        self.content=None
        self.stream=None
        self.network=patch('lyara.github_source.public_https',side_effect=self.destination)
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.temporary.cleanup()

    def destination(self,url):
        host=httpx.URL(url).host
        self.assertIn(host,{'api.github.com','codeload.github.com'})
        return {'hostname':host,'ip':'8.8.8.8','url':url}

    def dispatch(self,request):
        self.requests.append(request)
        self.assertEqual(request.url.host,'8.8.8.8')
        self.assertNotIn('authorization',request.headers)
        self.assertNotIn('cookie',request.headers)
        self.assertEqual(request.headers['host'],request.extensions['sni_hostname'])
        if '/commits/' in request.url.path:
            return httpx.Response(self.commit_status,json={'sha':self.resolve})
        if '/zipball/' in request.url.path:
            self.assertTrue(request.url.path.endswith(SHA),request.url)
            return httpx.Response(302,headers={'Location':self.location})
        headers={} if self.content_length is None else {'Content-Length':self.content_length}
        if self.stream is not None:return httpx.Response(self.archive_status,stream=self.stream,headers=headers)
        return httpx.Response(self.archive_status,content=self.content if self.content is not None else archive(self.files),headers=headers)

    def run_import(self,url='https://github.com/owner/repo',**kwargs):
        return import_github(url,self.root,self.vault,transport=httpx.MockTransport(self.dispatch),**kwargs)

    def test_resolves_commit_before_download_and_keeps_provenance(self):
        result=self.run_import('https://github.com/owner/repo/tree/main')
        self.assertEqual(len(self.requests),3)
        self.assertEqual(self.requests[0].url.path,'/repos/owner/repo/commits/main')
        self.assertEqual(result['github'],{'repository':'https://github.com/owner/repo','commit':SHA,'ref':'main'})
        self.assertEqual((upload_root(self.root,result['source'])/'index.html').read_text(),'Original website')
        metadata=json.loads((self.root/'uploads'/result['source'][7:]/'upload.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['github'],result['github'])
        self.assertFalse(list(self.root.glob('github-*')))

    def test_mutable_branch_cannot_change_imported_source(self):
        result=self.run_import(ref='feature/safe')
        first=upload_root(self.root,result['source'])/'index.html'
        self.resolve='a'*40
        self.files={'repo/index.html':'Different remote contents'}
        self.assertEqual(first.read_text(),'Original website')
        self.assertEqual(result['github']['commit'],SHA)
        self.assertIn(b'feature%2Fsafe',self.requests[0].url.raw_path)

    def test_env_uses_existing_encrypted_vault_and_is_removed(self):
        secret='private-github-import-example-1234'
        self.files['repo-'+SHA+'/.env']='DATABASE_URL='+secret
        self.files['repo-'+SHA+'/.env.example']='DATABASE_URL='
        result=self.run_import()
        root=upload_root(self.root,result['source'])
        self.assertFalse((root/'.env').exists())
        self.assertNotIn(secret,json.dumps(result))
        self.assertNotIn(secret,self.vault.path.read_bytes().decode('latin1'))
        self.assertEqual(self.vault.values('upload-'+result['source'][7:])['DATABASE_URL']['value'],secret)

    def test_rejects_user_urls_before_network(self):
        for url in ['http://github.com/owner/repo','https://github.com:443/owner/repo',
                    'https://user:secret@github.com/owner/repo','https://github.com.evil.test/owner/repo',
                    'https://github.com/owner/repo?token=private','https://github.com/owner/repo#x',
                    'https://github.com/owner/repo/issues','https://github.com/owner/%2e%2e',
                    'https://github.com/owner/repo/tree/../bad','https://127.0.0.1/owner/repo']:
            with self.subTest(url=url),self.assertRaises(ValueError):self.run_import(url)
        self.assertFalse(self.requests)

    def test_rate_limit_private_and_invalid_commit_never_download(self):
        for status in [403,404,429,500]:
            self.requests=[];self.commit_status=status
            with self.subTest(status=status),self.assertRaises(ValueError):self.run_import()
            self.assertEqual(len(self.requests),1)
        self.commit_status=200
        for sha in ['main','a'*39,'a'*41,None,{'sha':SHA}]:
            self.resolve=sha
            with self.subTest(sha=sha),self.assertRaises(ValueError):self.run_import()

    def test_never_follows_uncontrolled_or_wrong_commit_redirect(self):
        for location in ['http://codeload.github.com/owner/repo/zip/'+SHA,
                         'https://evil.example/owner/repo/zip/'+SHA,
                         'https://user:password@codeload.github.com/owner/repo/zip/'+SHA,
                         'https://codeload.github.com/owner/repo/zip/main',
                         'https://codeload.github.com/owner/other/zip/'+SHA,
                         'https://codeload.github.com/owner/repo/zip/'+SHA+'?secret=test']:
            self.requests=[];self.location=location
            with self.subTest(location=location),self.assertRaises(ValueError):self.run_import()
            self.assertEqual(len(self.requests),2)

    def test_archive_redirects_and_transport_errors_are_bounded(self):
        self.archive_status=302
        with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(len(self.requests),3)
        def fail(request):raise httpx.ReadTimeout('do not expose private credentials',request=request)
        with self.assertRaises(ValueError) as error:
            import_github('https://github.com/owner/repo',self.root,self.vault,transport=httpx.MockTransport(fail))
        self.assertNotIn('private credentials',str(error.exception))

    def test_compressed_upload_stream_and_content_length_are_bounded(self):
        with patch('lyara.github_source.MAX_UPLOAD',20):
            with self.assertRaises(ValueError):self.run_import()
        self.assertFalse(list(self.root.glob('github-*')))
        self.content_length='999999999'
        with self.assertRaises(ValueError):self.run_import()
        self.assertFalse((self.root/'uploads').exists())

    def test_stream_is_stopped_when_limit_reached_and_deadline_is_enforced(self):
        class Endless(httpx.SyncByteStream):
            def __init__(self):self.reads=0;self.closed=False
            def __iter__(self):
                for _ in range(20):
                    self.reads+=1
                    yield b'x'*65536
            def close(self):self.closed=True
        self.stream=Endless()
        with patch('lyara.github_source.MAX_UPLOAD',65540):
            with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(self.stream.reads,2)
        self.assertTrue(self.stream.closed)
        self.assertFalse(list(self.root.glob('github-*')))
        self.requests=[]
        with patch('lyara.github_source.time.monotonic',side_effect=[0,46]):
            with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(len(self.requests),1)

    def test_existing_zip_path_and_symlink_checks_are_preserved(self):
        for name in ['../escape','/absolute','site/CON.txt','site/name:stream']:
            self.files={'site/index.html':'Site',name:'bad'}
            with self.subTest(name=name),self.assertRaises(ValueError):self.run_import()
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as zipped:
            entry=zipfile.ZipInfo('site/link');entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16
            zipped.writestr(entry,'../../outside')
        self.content=output.getvalue()
        with self.assertRaises(ValueError):self.run_import()
        self.assertFalse(list((self.root/'uploads').iterdir()))

    def test_uploaded_code_and_package_hooks_are_never_executed(self):
        self.files={'repo/app.py':'raise RuntimeError("never run")',
                    'repo/package.json':'{"scripts":{"postinstall":"exit 1"}}'}
        result=self.run_import()
        self.assertTrue((upload_root(self.root,result['source'])/'app.py').exists())

    def test_private_dns_is_rejected_before_any_http_request(self):
        self.network.stop()
        with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):self.run_import()
        self.assertFalse(self.requests)
        self.network.start()


if __name__=='__main__':unittest.main()
