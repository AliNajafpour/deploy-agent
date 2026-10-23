import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import httpx

from lyara.project import inspect_project, snapshot
from lyara.liara import LiaraClient, LiaraError
from lyara.security import redact, workspace_path


class ProjectChecks(unittest.TestCase):
    def test_archive_excludes_secrets_dependencies_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'package.json').write_text('{"scripts":{"start":"node app.js"}}')
            (root / 'app.js').write_text('console.log("hello")')
            for name in ['.env', 'credentials.json', 'id_rsa', 'secrets.yaml']:
                (root / name).write_text('do-not-upload')
            (root / 'node_modules').mkdir()
            (root / 'node_modules' / 'secret.js').write_text('dependency')
            info = inspect_project(root)
            self.assertEqual(info['platform'], 'node')
            artifact = snapshot(root, root / '.lyara')
            with tarfile.open(artifact['path']) as archive:
                self.assertEqual(sorted(archive.getnames()), ['app.js', 'package.json'])
            self.assertEqual(len(artifact['sha256']), 64)

    def test_path_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                workspace_path(Path(tmp), '../outside')

    def test_secrets_redacted_recursively(self):
        data = {'envs': [{'key': 'DB_PASSWORD', 'value': 's3cr3t'}],
                'log': 'Authorization: Bearer sk-secretvalue password=hunter2'}
        text = json.dumps(redact(data))
        for secret in ['s3cr3t', 'sk-secretvalue', 'hunter2']:
            self.assertNotIn(secret, text)


class ClientChecks(unittest.TestCase):
    def test_ready_release_must_be_the_active_release_when_current_is_known(self):
        data={'releases':[{'_id':'new-release','state':'READY'}], 'currentRelease':'old-release'}
        with LiaraClient('test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
            with self.assertRaises(LiaraError) as raised:
                client.wait_release('sample-app','new-release',timeout=0)
            self.assertTrue(raised.exception.uncertain)

    def test_upload_and_release_follow_official_contract(self):
        requests = []
        def handle(request):
            requests.append((request.method, request.url.path, request.read()))
            self.assertEqual(request.headers['Authorization'], 'Bearer test-token')
            if request.url.path.endswith('/sources'):
                self.assertIn(b'name="file"', request.content)
                return httpx.Response(200, json={'sourceID': 'source-1'})
            return httpx.Response(200, json={'releaseID': 'release-1'})
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'source.tar.gz'
            archive.write_bytes(b'test-archive')
            with LiaraClient('test-token', transport=httpx.MockTransport(handle)) as client:
                source = client.upload('sample-app', archive)
                result = client.release('sample-app', source['sourceID'], 'node', 3000)
        self.assertEqual(result, {'releaseID': 'release-1'})
        self.assertEqual(requests[0][:2], ('POST', '/v2/projects/sample-app/sources'))
        self.assertEqual(json.loads(requests[1][2]), {'sourceID': 'source-1', 'type': 'node', 'port': 3000})

    def test_missing_metrics_remain_unavailable_not_zero(self):
        with LiaraClient('test', transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
            data = client.observe('sample-app')
        self.assertFalse(data['memory']['available'])
        self.assertEqual(data['memory']['status'], 403)

    def test_auth_errors_do_not_echo_provider_body(self):
        with LiaraClient('test', transport=httpx.MockTransport(lambda r: httpx.Response(401, text='token=secret'))) as client:
            with self.assertRaises(LiaraError) as raised:
                client.projects()
        self.assertEqual(raised.exception.status, 401)
        self.assertNotIn('secret', str(raised.exception))

    def test_environment_update_preserves_unrelated_variables(self):
        sent = []
        def handle(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'project': {'envs': [
                    {'key': 'KEEP', 'value': 'old'}, {'key': 'CHANGE', 'value': 'before'}]}})
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={})
        with LiaraClient('test', transport=httpx.MockTransport(handle)) as client:
            client.update_env('sample-app', {'CHANGE': 'after'})
        self.assertEqual(sent, [{'project': 'sample-app', 'variables': [
            {'key': 'KEEP', 'value': 'old'}, {'key': 'CHANGE', 'value': 'after'}]}])


if __name__ == '__main__':
    unittest.main()
