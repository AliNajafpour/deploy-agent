import json
import os
import tempfile
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from lyara.demo import DemoCloud, DemoPlanner
from lyara.models import Plan, RunRequest
from lyara.service import Assistant
from lyara.store import Store
from lyara.mcp_server import create_mcp


class SafetyChecks(unittest.TestCase):
    def test_resize_acknowledgement_without_actual_change_is_not_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cloud = DemoCloud()
            service = Assistant(tmp, Path(tmp) / '.lyara', cloud.client(), DemoPlanner())
            try:
                plan = Plan(summary='Resize', steps=[{'action':'resize','title':'Resize','reason':'Requested','args':{'plan_id':'larger-plan'}}])
                result = service.start(RunRequest(message='resize',project='demo-app',intent='operate'), plan)
                result = service.approve(result['id'], result['plan_digest'], True)
                self.assertEqual(result['status'], 'needs_attention')
                self.assertFalse(result['verification']['configuration_checks'][0]['matched'])
            finally:
                service.close()

    def test_requested_domain_is_the_default_health_target_after_deploy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'index.html').write_text('hello')
            cloud=DemoCloud()
            service=Assistant(root,root/'.lyara',cloud.client(),DemoPlanner())
            try:
                result=service.start(RunRequest(message='deploy',intent='deploy',project='new-app',
                    plan_id='demo-plan',port=80,domain='www.example.com'))
                result=service.approve(result['id'],result['plan_digest'],True)
                self.assertEqual(result['verification']['health']['url'],'https://www.example.com')
            finally:
                service.close()

    def test_deployment_plan_cannot_silently_omit_deployment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'index.html').write_text('hello')
            cloud = DemoCloud()
            service = Assistant(root, root / '.lyara', cloud.client(), DemoPlanner())
            try:
                result = service.start(RunRequest(message='Deploy', intent='deploy', project='new-app',
                    plan_id='demo-plan', port=80), Plan(summary='Looks good', steps=[]))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(cloud.mutations, [])
            finally:
                service.close()

    def test_custom_state_directory_never_enters_source_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'index.html').write_text('<p>hello</p>')
            service = Assistant(root, root / 'private-state', DemoCloud().client(), DemoPlanner())
            try:
                result = service.start(RunRequest(message='deploy', intent='deploy', project='new-app', plan_id='demo-plan', port=80))
                with tarfile.open(result['artifact']['path']) as archive:
                    self.assertEqual(archive.getnames(), ['index.html'])
            finally:
                service.close()

    def test_unchanged_secret_can_be_applied_after_approval(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LYARA_DEPLOY_DB_PASSWORD': 'fresh-private-value'}):
            cloud = DemoCloud()
            service = Assistant(tmp, Path(tmp) / '.lyara', cloud.client(), DemoPlanner())
            try:
                plan = Plan(summary='Update', steps=[{'action': 'update_env', 'title': 'Update', 'reason': 'Requested',
                    'args': {'env_refs': {'DB_PASSWORD': 'LYARA_DEPLOY_DB_PASSWORD'}}}])
                result = service.start(RunRequest(message='update', project='demo-app', intent='operate'), plan)
                result = service.approve(result['id'], result['plan_digest'], True)
                self.assertEqual(result['status'], 'completed')
                self.assertIn(('POST', '/v1/projects/update-envs'), cloud.mutations)
                self.assertNotIn('fresh-private-value', json.dumps(result))
            finally:
                service.close()

    def test_malformed_action_arguments_persist_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Assistant(tmp, Path(tmp) / '.lyara', DemoCloud().client(), DemoPlanner())
            try:
                plan = Plan(summary='Bad', steps=[{'action': 'redeploy', 'title': 'Bad', 'reason': 'Bad',
                    'args': {'source_id': 's', 'platform': [], 'port': 3000}}])
                result = service.start(RunRequest(message='test', project='demo-app', intent='operate'), plan)
                self.assertEqual(result['status'], 'failed')
            finally:
                service.close()

    def test_resume_pending_execution_after_process_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cloud = DemoCloud()
            service = Assistant(tmp, Path(tmp) / '.lyara', cloud.client(), DemoPlanner())
            plan = Plan(summary='Restart', steps=[{'action':'restart','title':'Restart','reason':'Requested','args':{}}])
            result = service.start(RunRequest(message='restart', project='demo-app', intent='operate'), plan)
            with patch.object(service, '_step', side_effect=SystemExit('simulate process exit')):
                with self.assertRaises(SystemExit):
                    service.approve(result['id'], result['plan_digest'], True)
            service.close()
            service = Assistant(tmp, Path(tmp) / '.lyara', cloud.client(), DemoPlanner())
            try:
                result = service.resume(result['id'])
                self.assertEqual(result['status'], 'needs_attention')
                self.assertIsNone(result['verification']['action_checks'][0]['matched'])
                self.assertEqual(cloud.mutations, [('POST','/v1/projects/demo-app/actions/restart')])
            finally:
                service.close()

    def test_intentional_shutdown_verifies_stopped_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cloud = DemoCloud()
            client = cloud.client()
            client.health = lambda url: {'available':False,'healthy':False,'url':url}
            service = Assistant(tmp, Path(tmp) / '.lyara', client, DemoPlanner())
            try:
                plan = Plan(summary='Stop', steps=[{'action':'scale','title':'Stop','reason':'Requested','args':{'scale':0}}])
                result = service.start(RunRequest(message='stop',project='demo-app',intent='operate'), plan)
                result = service.approve(result['id'], result['plan_digest'], True)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['verification']['outcome'], 'stopped')
            finally:
                service.close()

    def test_unknown_write_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'journal.sqlite')
            calls = []
            def ambiguous():
                calls.append(1)
                from lyara.liara import LiaraError
                raise LiaraError(0, 'lost response', uncertain=True)
            with self.assertRaises(RuntimeError):
                store.write_once('action-1', ambiguous)
            with self.assertRaises(RuntimeError):
                store.write_once('action-1', ambiguous)
            self.assertEqual(len(calls), 1)

    def test_secret_reference_survives_redaction_without_exposing_value(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LYARA_DEPLOY_DB_PASSWORD': 'fresh-private-value'}):
            cloud = DemoCloud()
            service = Assistant(tmp, Path(tmp) / '.lyara', cloud.client(), DemoPlanner())
            try:
                plan = Plan(summary='Update password via server secret reference', steps=[{
                    'action': 'update_env', 'title': 'Update environment', 'reason': 'User requested',
                    'args': {'env_refs': {'DB_PASSWORD': 'LYARA_DEPLOY_DB_PASSWORD'}}}])
                result = service.start(RunRequest(message='update', project='demo-app', intent='operate'), plan)
                self.assertEqual(result['status'], 'awaiting_approval')
                self.assertEqual(result['plan']['steps'][0]['args']['env_refs'], {'DB_PASSWORD': 'LYARA_DEPLOY_DB_PASSWORD'})
                self.assertNotIn('fresh-private-value', json.dumps(result))
            finally:
                service.close()

    def test_changed_secret_requires_a_new_plan(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LYARA_DEPLOY_DB_PASSWORD': 'original-private-value'}):
            cloud = DemoCloud()
            service = Assistant(tmp, Path(tmp) / '.lyara', cloud.client(), DemoPlanner())
            try:
                plan = Plan(summary='Update', steps=[{'action': 'update_env', 'title': 'Update', 'reason': 'Requested',
                    'args': {'env_refs': {'DB_PASSWORD': 'LYARA_DEPLOY_DB_PASSWORD'}}}])
                result = service.start(RunRequest(message='update', project='demo-app', intent='operate'), plan)
                os.environ['LYARA_DEPLOY_DB_PASSWORD'] = 'changed-private-value'
                result = service.approve(result['id'], result['plan_digest'], True)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(cloud.mutations, [])
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
