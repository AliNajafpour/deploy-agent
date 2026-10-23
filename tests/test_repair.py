import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyara.demo import DemoCloud
from lyara.models import Plan, RunRequest
from lyara.service import Assistant


class RepairChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = '{"scripts":{"start":"node absent.js"},"dependencies":{}}\n'
        (self.root / 'package.json').write_text(self.original, encoding='utf8')
        (self.root / 'server.mjs').write_text('console.log("startup");\n', encoding='utf8')
        self.cloud = DemoCloud()
        self.service = Assistant(self.root, self.root / '.lyara', self.cloud.client(), None)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_repair_builder_accepts_bind_fix_but_rejects_response_changes(self):
        import io
        from tests.repair_agent import RepairBuilder,PROJECTS
        builder=object.__new__(RepairBuilder)
        builder.fixture='02-missing-package'
        builder.app=PROJECTS/builder.fixture
        files={p.name:p.read_bytes() for p in builder.app.iterdir() if p.is_file()}
        files['requirements.txt']=b'colorama==0.4.6\n'
        files['app.py']=files['app.py'].replace(b'127.0.0.1',b'0.0.0.0')
        def bundle():
            output=io.BytesIO()
            with tarfile.open(fileobj=output,mode='w:gz') as archive:
                for name,content in files.items():
                    member=tarfile.TarInfo(name);member.size=len(content)
                    archive.addfile(member,io.BytesIO(content))
            return output.getvalue()
        builder.inspect_archive(bundle())
        files['app.py']=files['app.py'].replace(b'missing dependency repaired',b'changed website response')
        with self.assertRaises(AssertionError):builder.inspect_archive(bundle())

    def edit(self, name='package.json', content=None):
        path = self.root / name
        return {'path': name, 'before_sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                'content': content if content is not None else '{"scripts":{"start":"node server.mjs"},"dependencies":{}}\n'}

    def start(self, edits=None, intent='repair'):
        request = RunRequest(message='Fix only the deployment startup failure', intent=intent,
                             project='demo-app', platform='node', port=3000)
        plan = Plan(summary='Correct the startup command', steps=[{'action': 'repair_deploy',
                    'title': 'Repair startup and rebuild', 'reason': 'The configured entrypoint is missing',
                    'args': {'platform': 'node', 'port': 3000, 'files': edits or [self.edit()]}}])
        return self.service.start(request, plan)

    def test_repair_previews_exact_diff_without_editing_original_or_cloud(self):
        run = self.start()
        self.assertEqual(run['status'], 'awaiting_approval', run.get('error'))
        self.assertEqual(self.cloud.mutations, [])
        self.assertEqual((self.root / 'package.json').read_text(), self.original)
        repair = run['repair']
        self.assertIn('-'+self.original.strip(), repair['diff'])
        self.assertIn('node server.mjs', repair['diff'])
        self.assertNotEqual(repair['base_sha256'], run['artifact']['sha256'])
        self.assertTrue(repair['checks'])
        with tarfile.open(run['artifact']['path']) as archive:
            self.assertEqual(archive.extractfile('package.json').read().decode(), self.edit()['content'])
        self.assertEqual(self.service.report(run['id'])['repair']['diff'], repair['diff'])

    def test_rejected_repair_keeps_source_and_does_not_publish(self):
        run = self.start()
        run = self.service.approve(run['id'], run['plan_digest'], False)
        self.assertEqual(run['status'], 'rejected')
        self.assertEqual(self.cloud.mutations, [])
        self.assertEqual((self.root / 'package.json').read_text(), self.original)

    def test_approved_repair_uploads_only_frozen_candidate_and_survives_restart(self):
        run = self.start()
        (self.root / 'package.json').write_text('changed after review')
        self.service.close()
        self.service = Assistant(self.root, self.root / '.lyara', self.cloud.client(), None)
        with patch.object(self.service.cloud, 'upload', wraps=self.service.cloud.upload) as upload:
            result = self.service.approve(run['id'], run['plan_digest'], True)
            self.assertEqual(result['status'], 'completed', result.get('error'))
            self.assertEqual(str(upload.call_args.args[1]), run['artifact']['path'])
        count = len(self.cloud.mutations)
        self.service.approve(run['id'], run['plan_digest'], True)
        self.assertEqual(len(self.cloud.mutations), count)
        self.assertEqual((self.root / 'package.json').read_text(), 'changed after review')
        self.assertTrue(result['verification']['action_checks'][0]['matched'])
        self.assertEqual(self.service.store.known_release('demo-app', result['results'][0]['releaseID'])['platform'], 'node')

    def test_paths_outside_deployment_scope_are_rejected(self):
        for name in ['../package.json', '/package.json', 'C:/package.json', '.env', 'index.html',
                     'frontend/package.json', 'node_modules/package.json', 'backend/../../server.py']:
            with self.subTest(name=name):
                run = self.start([{'path': name, 'before_sha256': None, 'content': '{}'}])
                self.assertEqual(run['status'], 'failed')
                self.assertEqual(self.cloud.mutations, [])

    def test_stale_hash_duplicate_noop_and_out_of_scope_intent_are_rejected(self):
        bad = self.edit()
        bad['before_sha256'] = '0'*64
        for edits in [[bad], [self.edit(), self.edit()], [self.edit(content=self.original)]]:
            with self.subTest(edits=edits):
                run = self.start(edits)
                self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.start(intent='operate')['status'], 'failed')
        self.assertEqual(self.cloud.mutations, [])

    def test_invalid_json_python_and_secrets_are_not_publishable(self):
        for edit in [self.edit(content='{bad json'),
                     self.edit('app.py', 'def broken(:\n'),
                     self.edit('Procfile', 'web: password=secret-value')]:
            with self.subTest(path=edit['path']):
                run = self.start([edit])
                self.assertEqual(run['status'], 'failed')
                self.assertNotIn('secret-value', json.dumps(run))
        self.assertEqual(self.cloud.mutations, [])

    def test_missing_python_dependency_can_be_added_to_candidate(self):
        run = self.start([self.edit('requirements.txt', 'colorama==0.4.6\n')])
        self.assertEqual(run['status'], 'awaiting_approval', run.get('error'))
        self.assertFalse((self.root / 'requirements.txt').exists())
        self.assertIn('requirements.txt', run['artifact']['files'])
        self.assertIn('install', run['repair']['dependency_note'].lower())

    def test_lockfile_must_match_dependency_changes(self):
        lock = {'name': 'sample', 'lockfileVersion': 3, 'packages': {'': {'dependencies': {}}}}
        (self.root / 'package-lock.json').write_text(json.dumps(lock))
        run = self.start([self.edit(content='{"scripts":{"start":"node server.mjs"},"dependencies":{"colorama":"1.0.0"}}')])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.cloud.mutations, [])

    def test_secret_source_is_never_exposed_to_planner(self):
        (self.root / 'Procfile').write_text('web: password=hidden-source-value')
        observed = {}
        class Planner:
            def plan(inner, request, context):
                observed.update(context)
                return Plan(summary='Need a safe startup file', questions=['What is the public startup command?'])
        self.service.planner = Planner()
        run = self.service.start(RunRequest(message='Repair deployment', intent='repair', project='demo-app', port=3000))
        self.assertEqual(run['status'], 'needs_input')
        self.assertNotIn('hidden-source-value', json.dumps(observed))
        self.assertIn('package.json', observed['deployment_files'])


    def test_ignored_edit_cannot_be_approved_as_if_it_will_be_published(self):
        (self.root / '.liaraignore').write_text('package.json\n')
        edit = self.edit()
        edit['before_sha256'] = None
        run = self.start([edit])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.cloud.mutations, [])

    def test_lock_root_without_resolved_graph_is_not_accepted(self):
        lock = {'lockfileVersion': 3, 'packages': {'': {'dependencies': {'express': '4.21.2'}}}}
        (self.root / 'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{'':{}}}))
        run = self.start([self.edit(content='{"scripts":{"start":"node server.mjs"},"dependencies":{"express":"4.21.2"}}'),
                          self.edit('package-lock.json', json.dumps(lock))])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.cloud.mutations, [])

    def test_broken_original_manifest_can_be_repaired(self):
        (self.root / 'package.json').write_text('{"scripts": BROKEN}')
        run = self.start()
        self.assertEqual(run['status'], 'awaiting_approval', run.get('error'))
        self.assertIn('BROKEN', run['repair']['diff'])

    def test_prefixed_secret_assignments_are_omitted_from_model_context(self):
        (self.root / 'app.py').write_text('SECRET_KEY = "session-signing-value"\nAWS_SECRET_ACCESS_KEY = "cloud-secret-value"\n')
        seen = {}
        class Planner:
            def plan(inner, request, context):
                seen.update(context)
                return Plan(summary='Inspect deployment only', questions=['Need public startup configuration'])
        self.service.planner = Planner()
        run = self.service.start(RunRequest(message='Repair startup', intent='repair', project='demo-app', port=3000))
        self.assertEqual(run['status'], 'needs_input')
        self.assertNotIn('session-signing-value', json.dumps(seen))
        self.assertNotIn('cloud-secret-value', json.dumps(seen))
        self.assertIn('app.py', seen['omitted_files'])


    def test_quoted_secret_assignment_is_not_sent_as_source(self):
        (self.root / 'main.py').write_text('settings = {"SECRET_KEY": "quoted-signing-value"}\n')
        seen = {}
        class Planner:
            def plan(inner, request, context):
                seen.update(context)
                return Plan(summary='Read only', questions=['Need non-secret startup configuration'])
        self.service.planner = Planner()
        self.service.start(RunRequest(message='Repair', intent='repair', project='demo-app', port=3000))
        self.assertNotIn('quoted-signing-value', json.dumps(seen))

    def test_staging_preserves_executable_mode_for_unchanged_backend_scripts(self):
        import io
        from lyara.project import snapshot
        from lyara.repair import deployment_files, stage_repair
        base = snapshot(self.root, self.root / '.lyara' / 'artifacts')
        with tarfile.open(base['path']) as archive:
            records = [(member, archive.extractfile(member).read()) for member in archive.getmembers()]
        with tarfile.open(base['path'], 'w:gz') as archive:
            for member, data in records:
                if member.name == 'server.mjs':
                    member.mode = 0o755
                archive.addfile(member, io.BytesIO(data))
        base['sha256'] = hashlib.sha256(Path(base['path']).read_bytes()).hexdigest()
        files, _ = deployment_files(base)
        candidate, _ = stage_repair(base, [self.edit()], files, self.root / '.lyara' / 'out')
        with tarfile.open(candidate['path']) as archive:
            self.assertEqual(archive.getmember('server.mjs').mode & 0o777, 0o755)


    def test_bracket_secret_assignment_is_omitted(self):
        from lyara.project import snapshot
        from lyara.repair import deployment_files
        (self.root / 'app.py').write_text('app.config["SECRET_KEY"] = "flask-signing-value"\n')
        files, omitted = deployment_files(snapshot(self.root, self.root / '.lyara' / 'artifacts'))
        self.assertNotIn('flask-signing-value', json.dumps(files))
        self.assertIn('app.py', omitted)

    def test_lockfile_only_edits_cannot_bypass_dependency_policy(self):
        (self.root / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{"":{}}}')
        run = self.start([self.edit('package-lock.json', '{"lockfileVersion":3,"packages":{}}')])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.cloud.mutations, [])


if __name__ == '__main__':
    unittest.main()
