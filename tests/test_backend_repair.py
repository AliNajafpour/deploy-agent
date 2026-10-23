import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from lyara.demo import DemoCloud
from lyara.models import Plan, RunRequest
from lyara.project import snapshot
from lyara.repair import MAX_TOTAL, deployment_files, stage_repair, validate_edits
from lyara.service import Assistant


HANDLER = '''from fastapi import FastAPI
app = FastAPI()
@app.post("/summary")
def summary(values: list[float]):
    return {"average": sum(values) / len(values)}
'''
FIXED_HANDLER = HANDLER.replace('sum(values) / len(values)', 'sum(values) / len(values) if values else 0')
HANDLER_PATH = 'backend/routes/summary.py'


class BackendRepairChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'backend/routes').mkdir(parents=True)
        (self.root / HANDLER_PATH).write_text(HANDLER, encoding='utf8')
        (self.root / 'requirements.txt').write_text('fastapi==0.115.0\n', encoding='utf8')
        self.cloud = DemoCloud()
        self.service = Assistant(self.root, self.root / '.lyara', self.cloud.client(), None)
        self.addCleanup(self.service.close)

    def edit(self, name=HANDLER_PATH, content=FIXED_HANDLER):
        path = self.root / name
        return {'path': name, 'before_sha256': hashlib.sha256(path.read_bytes()).hexdigest()
                if path.exists() else None, 'content': content}

    def start(self, edit=None):
        plan = Plan(summary='اصلاح خطای سایت', evidence=['before.logs: ZeroDivisionError in summary'],
            steps=[{'action': 'repair_deploy', 'title': 'اصلاح و انتشار',
                    'reason': 'فهرست خالی خطا ایجاد می‌کند.', 'args': {
                        'platform': 'python', 'port': 3000, 'files': [edit or self.edit()]}}])
        return self.service.start(RunRequest(message='Fix the HTTP 500 for an empty summary',
            intent='repair', project='demo-app', platform='python', port=3000), plan)

    @staticmethod
    def response(source, values):
        # Only these two test-owned constants execute. Staged/uploaded source never does.
        if source not in (HANDLER, FIXED_HANDLER):
            raise AssertionError('Not a trusted fixture')
        namespace = {}
        exec(compile(source, '<trusted-test-fixture>', 'exec'), namespace)
        with TestClient(namespace['app'], raise_server_exceptions=False) as client:
            return client.post('/summary', json=values)

    def test_nested_http500_fix_is_staged_then_only_approved_bytes_upload(self):
        self.assertEqual(self.response(HANDLER, []).status_code, 500)
        run = self.start()
        self.assertEqual(run['status'], 'awaiting_approval', run.get('error'))
        self.assertEqual(self.cloud.mutations, [])
        self.assertEqual((self.root / HANDLER_PATH).read_text(), HANDLER)
        with tarfile.open(run['artifact']['path']) as archive:
            staged = archive.extractfile(HANDLER_PATH).read()
            self.assertEqual(staged.decode(), FIXED_HANDLER)
        self.assertEqual(self.response(FIXED_HANDLER, []).json(), {'average': 0})
        self.assertEqual(self.response(FIXED_HANDLER, [2, 4]).json(), {'average': 3})
        self.assertEqual(run['repair']['changes'][0]['before_sha256'], self.edit()['before_sha256'])
        self.assertEqual(run['repair']['changes'][0]['after_sha256'], hashlib.sha256(staged).hexdigest())
        self.assertIn('if values else 0', run['repair']['diff'])
        original_upload = self.service.cloud.upload
        uploaded = []
        def upload(project, path):
            uploaded.append(Path(path).read_bytes())
            return original_upload(project, path)
        (self.root / HANDLER_PATH).write_text('# changed after review\n')
        with patch.object(self.service.cloud, 'upload', side_effect=upload):
            result = self.service.approve(run['id'], run['plan_digest'], True)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(hashlib.sha256(uploaded[0]).hexdigest(), run['artifact']['sha256'])
        self.assertEqual((self.root / HANDLER_PATH).read_text(), '# changed after review\n')

    def test_rejected_backend_candidate_never_changes_source_or_cloud(self):
        run = self.start()
        self.assertEqual(run['status'], 'awaiting_approval', run.get('error'))
        result = self.service.approve(run['id'], run['plan_digest'], False)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.cloud.mutations, [])
        self.assertEqual((self.root / HANDLER_PATH).read_text(), HANDLER)

    def test_existing_backend_hash_and_syntax_are_required(self):
        stale = self.edit()
        stale['before_sha256'] = '0' * 64
        for edit in (stale, self.edit(content='def broken(:\n')):
            with self.subTest(edit=edit):
                self.assertEqual(self.start(edit)['status'], 'failed')
        self.assertEqual((self.root / HANDLER_PATH).read_text(), HANDLER)
        self.assertEqual(self.cloud.mutations, [])

    def test_backend_extraction_is_opt_in_and_prioritizes_traceback_with_manifests(self):
        for name in ['backend/a.py', 'backend/b.py', 'backend/c.py']:
            (self.root / name).write_text('# x\n' * 5900, encoding='utf8')
        (self.root / 'backend/routes/lookup.js').write_text('export const lookup = () => null;\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/page.js').write_text('export default "ui";\n')
        base = snapshot(self.root, self.root / '.lyara/artifacts')
        ordinary, _ = deployment_files(base)
        self.assertNotIn(HANDLER_PATH, ordinary)
        observations = {'logs': {'data': [{'message': 'File "/app/' + HANDLER_PATH + '", line 5\nZeroDivisionError'}]}}
        files, omitted = deployment_files(base, include_backend=True, observations=observations)
        self.assertIn(HANDLER_PATH, files)
        self.assertIn('requirements.txt', files)
        self.assertIn('backend/routes/lookup.js', files)
        self.assertNotIn('src/page.js', files)
        self.assertTrue(omitted)
        self.assertLessEqual(sum(len(item['content'].encode()) for item in files.values()), MAX_TOTAL)

    def test_forbidden_existing_paths_and_new_backend_files_are_rejected(self):
        forbidden = ['../route.py', '/route.py', 'C:/route.py', 'backend/../route.py',
            'backend\\route.py', 'frontend/route.py', 'src/page.js', 'backend/migrations/001.py',
            'backend/tests/route.py', 'backend/test_route.py', 'backend/.private/route.py',
            'backend/credentials.py', 'backend/secrets/route.py', 'backend/node_modules/x.js',
            'backend/views/page.js', 'backend/routes/new.py']
        for name in forbidden:
            with self.subTest(name=name):
                known = {} if name.endswith('new.py') else {name: {'sha256': '0' * 64, 'content': 'old'}}
                edit = {'path': name, 'before_sha256': None if not known else '0' * 64, 'content': 'x = 1\n'}
                with self.assertRaises(ValueError):
                    validate_edits([edit], known)
        with self.assertRaises(ValueError):
            validate_edits([{'path': [], 'before_sha256': None, 'content': 'x = 1\n'}], {})

    def test_backend_secrets_are_omitted_and_cannot_be_inserted(self):
        (self.root / 'backend/config.py').write_text('SECRET_KEY = "hidden-source-value"\n')
        (self.root / 'backend/private.py').write_text('value = "opaque-existing-secret"\n')
        base = snapshot(self.root, self.root / '.lyara/artifacts')
        files, omitted = deployment_files(base, ['opaque-existing-secret'], include_backend=True)
        self.assertIn('backend/config.py', omitted)
        self.assertIn('backend/private.py', omitted)
        self.assertNotIn('hidden-source-value', json.dumps(files))
        self.assertNotIn('opaque-existing-secret', json.dumps(files))
        for content in ('SECRET_KEY = "new-secret-value"\n', 'value = "opaque-existing-secret"\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                stage_repair(base, [self.edit(content=content)], files,
                             self.root / '.lyara/out', ['opaque-existing-secret'])

    def test_traceback_host_paths_cannot_add_files_to_context(self):
        host = self.root / '.lyara/host.py'
        host.parent.mkdir(exist_ok=True)
        host.write_text('value = "outside-artifact"\n')
        base = snapshot(self.root, self.root / '.lyara/artifacts')
        files, _ = deployment_files(base, include_backend=True,
            observations={'logs': 'File "' + str(host) + '", line 1; File "/etc/passwd", line 2'})
        self.assertNotIn('outside-artifact', json.dumps(files))
        self.assertNotIn('host.py', files)

    def test_backend_syntax_check_never_runs_module_body(self):
        base = snapshot(self.root, self.root / '.lyara/artifacts')
        files, _ = deployment_files(base, include_backend=True)
        candidate, review = stage_repair(base,
            [self.edit(content='raise RuntimeError("module must never run on the assistant host")\n')],
            files, self.root / '.lyara/out')
        self.assertIn(HANDLER_PATH, candidate['files'])
        self.assertEqual(review['checks'], [{'path': HANDLER_PATH, 'check': 'Python syntax', 'passed': True}])
        self.assertEqual((self.root / HANDLER_PATH).read_text(), HANDLER)

    @unittest.skipUnless(shutil.which('git'), 'Git is needed to verify downloadable patches.')
    def test_unterminated_diff_lines_apply_with_exact_final_newline_bytes(self):
        for before, after in (('value = 1', 'value = 2'),
                              ('value = 1\n', 'value = 2'),
                              ('value = 1', 'value = 2\n'),
                              ('value = 1\nsame = True', 'value = 2\nsame = True')):
            with self.subTest(before=before, after=after):
                (self.root / HANDLER_PATH).write_bytes(before.encode())
                base = snapshot(self.root, self.root / '.lyara/artifacts')
                files, _ = deployment_files(base, include_backend=True)
                candidate, review = stage_repair(base, [self.edit(content=after)], files,
                                                  self.root / '.lyara/out')
                with tempfile.TemporaryDirectory(prefix='lyara-diff-check-') as directory:
                    work = Path(directory)
                    target = work / HANDLER_PATH
                    target.parent.mkdir(parents=True)
                    target.write_bytes(before.encode())
                    command = [shutil.which('git'), '-c', 'safe.directory=' + work.as_posix(),
                               '-c', 'core.autocrlf=false']
                    subprocess.run(command + ['init', '--quiet'], cwd=work, capture_output=True, check=True)
                    for args in (['apply', '--check', '-'], ['apply', '-']):
                        applied = subprocess.run(command + args, input=review['diff'].encode(),
                            cwd=work, capture_output=True)
                        self.assertEqual(applied.returncode, 0, applied.stderr.decode(errors='replace'))
                    self.assertEqual(target.read_bytes(), after.encode())
                with tarfile.open(candidate['path']) as archive:
                    self.assertEqual(archive.extractfile(HANDLER_PATH).read(), after.encode())
                self.assertEqual((self.root / HANDLER_PATH).read_bytes(), before.encode())


if __name__ == '__main__':
    unittest.main()
