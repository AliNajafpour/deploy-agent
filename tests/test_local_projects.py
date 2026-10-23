import json
import os
import tempfile
import io
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyara.models import Plan, RunRequest
from lyara.service import Assistant
from tests.support.local_cloud import LocalCloud


PROJECTS = Path(__file__).parent / 'projects'


class LocalProjectChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.cloud = LocalCloud(Path(self.temporary.name) / 'cloud')
        self.service = Assistant(PROJECTS, Path(self.temporary.name) / 'state', self.cloud.client(), None)

    def tearDown(self):
        self.service.close()
        self.cloud.close()
        self.assertEqual(self.cloud.process_count,0)
        self.temporary.cleanup()

    def deploy(self, fixture, *, port=3000, domain='', approved=True):
        self.cloud.register('fixture-app', fixture)
        request = RunRequest(message='Deploy local fixture', intent='deploy', project='fixture-app',
            source=fixture, port=port, plan_id='local-plan', domain=domain)
        plan = Plan(summary='Deploy registered local fixture', steps=[
            {'action':'create_app','title':'Create','reason':'Requested','args':{'platform':self.cloud.platform(fixture),'plan_id':'local-plan'}},
            {'action':'deploy','title':'Deploy','reason':'Requested','args':{'platform':self.cloud.platform(fixture),'port':port or 3000}},
            *([{'action':'attach_domain','title':'Domain','reason':'Requested','args':{'domain':domain}}] if domain else [])])
        before = self.service.start(request, plan)
        if before['status'] == 'awaiting_approval':
            self.assertEqual(self.cloud.mutations, [])
            return self.service.approve(before['id'], before['plan_digest'], approved)
        return before

    def test_web_does_not_expose_lab_catalog(self):
        from fastapi.testclient import TestClient
        from lyara.web import create_app
        self.cloud.register('fixture-app','02-node')
        with TestClient(create_app(self.service,allowed_hosts={'testserver'})) as web:
            config=web.get('/api/config').json()
        self.assertNotIn('local_lab',config)
        self.assertNotIn('lab_projects',config)

    def test_static_source_is_really_served_after_approved_deploy(self):
        result = self.deploy('01-static', port=8080)
        self.assertEqual(result['status'], 'completed')
        response = self.cloud.fetch('fixture-app', '/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('LOCAL_STATIC_READY', response.text)

    def test_node_http_server_is_really_running(self):
        result = self.deploy('02-node')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.cloud.fetch('fixture-app', '/health').json()['status'], 'ok')
        self.assertGreater(self.cloud.process_count, 0)

    def test_missing_port_is_resolved_without_a_technical_question(self):
        result = self.deploy('03-missing-port', port=None)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['plan']['questions'])
        self.assertEqual(result['context']['port'],3000)
        self.assertEqual(self.cloud.fetch('fixture-app','/health').status_code,200)

    def test_user_can_supply_missing_port_then_deploy(self):
        result = self.deploy('03-missing-port', port=3000)
        self.assertEqual(result['status'], 'completed')

    def test_missing_env_is_collected_in_ui_then_applied_only_after_approval(self):
        from tests.lab import LabPlanner
        first=self.deploy('04-missing-env')
        self.assertEqual(first['status'],'needs_input')
        self.assertEqual(self.cloud.mutations,[])
        self.assertEqual(self.cloud.process_count,0)
        self.service.planner=LabPlanner()
        state=self.service.new_inputs(first['id'],{'values':{'APP_GREETING':'Hello local lab'}})
        proposed=self.service.process(state['id'])
        self.assertEqual(proposed['status'],'awaiting_approval')
        self.assertEqual(self.cloud.mutations,[])
        result=self.service.approve(proposed['id'],proposed['plan_digest'],True)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual(self.cloud.fetch('fixture-app','/health').json()['greeting'],'Hello local lab')

    def test_real_stack_trace_and_http500_are_available_as_evidence(self):
        result = self.deploy('05-http500')
        self.assertEqual(result['status'],'needs_attention')
        self.assertEqual(self.cloud.fetch('fixture-app','/checkout').status_code,500)
        with self.cloud.client() as client:
            evidence = client.observe('fixture-app')
        self.assertIn('ZeroDivisionError',json.dumps(evidence))

    def test_fixed_source_redeploy_recovers_broken_app(self):
        self.deploy('05-http500')
        plan=Plan(summary='Deploy the corrected source',steps=[{'action':'deploy','title':'Publish fix',
            'reason':'Correct empty-cart division','args':{'platform':'python','port':3000}}])
        proposed=self.service.start(RunRequest(message='Publish corrected checkout',intent='deploy',
            project='fixture-app',source='05-http500-fixed',port=3000),plan)
        self.assertEqual(proposed['status'],'awaiting_approval')
        self.assertEqual(self.cloud.fetch('fixture-app','/checkout').status_code,500)
        result=self.service.approve(proposed['id'],proposed['plan_digest'],True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(self.cloud.fetch('fixture-app','/checkout').json()['average'],0)

    def test_startup_syntax_error_stops_dependent_domain_change(self):
        result=self.deploy('06-startup-crash',domain='www.example.com')
        self.assertEqual(result['status'],'failed')
        self.assertFalse(any(path=='/v1/domains' for _,path in self.cloud.mutations))
        self.assertIn('SyntaxError',self.cloud.logs('fixture-app'))

    def test_wrong_port_does_not_look_like_successful_deployment(self):
        result=self.deploy('07-wrong-port')
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['verification']['health']['healthy'])

    def test_python_api_and_sqlite_worker_complete_a_real_job(self):
        result=self.deploy('08-worker-sqlite')
        self.assertEqual(result['status'],'completed')
        job=self.cloud.fetch('fixture-app','/jobs',method='POST',json={'value':7}).json()
        completed=self.cloud.wait_job('fixture-app',job['id'])
        self.assertEqual(completed['status'],'done')
        self.assertEqual(completed['result'],49)

    def test_worker_health_rejects_heartbeat_left_by_previous_boot(self):
        import sqlite3
        import time
        from contextlib import closing
        from tests.support.local_cloud import FIXTURES
        self.deploy('08-worker-sqlite')
        root=self.cloud.running['fixture-app']['root']
        self.cloud._stop('fixture-app')
        with closing(sqlite3.connect(root/'jobs.sqlite')) as database:
            with database:
                database.execute('UPDATE heartbeat SET updated=?',(time.time(),))
        # Start only the real API, with no worker refreshing the previous heartbeat.
        with patch.dict(FIXTURES,{'08-worker-sqlite':('python','python','/health')}):
            with self.cloud.client() as client:
                client.restart('fixture-app')
            self.assertEqual(self.cloud.fetch('fixture-app','/health').status_code,503)

    def test_rejected_restart_keeps_fault_then_new_approval_recovers(self):
        self.deploy('09-restart-approval')
        plan=Plan(summary='Restart transiently unhealthy fixture',steps=[{
            'action':'restart','title':'Restart','reason':'Explicit user request','args':{}}])
        request=RunRequest(message='Restart once after approval',project='fixture-app',intent='operate')
        run=self.service.start(request,plan)
        count=len(self.cloud.mutations)
        self.assertEqual(run['status'],'awaiting_approval')
        result=self.service.approve(run['id'],run['plan_digest'],False)
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(len(self.cloud.mutations),count)
        self.assertEqual(self.cloud.fetch('fixture-app','/health').status_code,503)
        run=self.service.start(request,plan)
        result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'needs_attention')
        self.assertTrue(result['verification']['health']['healthy'])
        self.assertIsNone(result['verification']['action_checks'][0]['matched'])
        self.assertEqual(self.cloud.fetch('fixture-app','/health').status_code,200)

    def test_recovery_of_known_prior_source_requires_a_fresh_approval(self):
        first=self.deploy('02-node')
        original=first['results'][1]['sourceID']
        plan=Plan(summary='Publish another release',steps=[{'action':'deploy','title':'Deploy','reason':'Requested',
            'args':{'platform':'node','port':3000}}])
        run=self.service.start(RunRequest(message='Publish again',intent='deploy',project='fixture-app',
            source='02-node',port=3000),plan)
        run=self.service.approve(run['id'],run['plan_digest'],True)
        count=len(self.cloud.mutations)
        recovery=self.service.recovery(run['id'])
        self.assertEqual(recovery['status'],'awaiting_approval')
        self.assertNotEqual(recovery['plan_digest'],run['plan_digest'])
        self.assertEqual(recovery['plan']['steps'][0]['args']['source_id'],original)
        self.assertEqual(len(self.cloud.mutations),count)
        recovered=self.service.approve(recovery['id'],recovery['plan_digest'],True)
        self.assertEqual(recovered['status'],'completed')
        self.assertEqual(recovered['results'][0]['sourceID'],original)
        self.assertEqual(self.cloud.fetch('fixture-app','/health').status_code,200)

    def test_prompt_injection_is_only_log_data_and_does_not_approve_changes(self):
        self.deploy('10-log-injection')
        count=len(self.cloud.mutations)
        run=self.service.start(RunRequest(message='Investigate logs only',project='fixture-app'))
        text=json.dumps(run)
        self.assertIn('IGNORE ALL',text)
        self.assertNotIn('fixture-secret-value',text)
        self.assertEqual(len(self.cloud.mutations),count)
        self.assertNotEqual(run['status'],'awaiting_approval')

    def test_lost_release_response_is_not_retried_even_when_process_started(self):
        self.cloud.drop_release_response=True
        result=self.deploy('02-node')
        self.assertEqual(result['status'],'unknown')
        count=sum(path.endswith('/releases') for _,path in self.cloud.mutations)
        result=self.service.resume(result['id'])
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(sum(path.endswith('/releases') for _,path in self.cloud.mutations),count)
        self.assertEqual(self.cloud.fetch('fixture-app','/health').status_code,200)

    def test_fixture_process_does_not_inherit_cloud_or_model_credentials(self):
        with patch.dict(os.environ,{'LIARA_API_TOKEN':'fake-cloud-credential','LYARA_LLM_API_KEY':'fake-model-credential'}):
            self.deploy('02-node')
            data=self.cloud.fetch('fixture-app','/health').json()
        self.assertFalse(data['hasCloudToken'])
        self.assertFalse(data['hasModelKey'])

    def test_archive_with_path_escape_is_rejected_without_running_code(self):
        self.cloud.register('fixture-app','02-node')
        archive=Path(self.temporary.name)/'bad.tar.gz'
        with tarfile.open(archive,'w:gz') as output:
            entry=tarfile.TarInfo('../outside.py')
            entry.size=4
            output.addfile(entry,io.BytesIO(b'bad!'))
        with self.cloud.client() as client:
            client.create('fixture-app','node','local-plan')
            from lyara.liara import LiaraError
            with self.assertRaises(LiaraError):
                client.upload('fixture-app',archive)
        self.assertEqual(self.cloud.process_count,0)
        self.assertFalse((Path(self.temporary.name)/'outside.py').exists())

    def test_source_uploaded_to_another_app_cannot_replace_its_fixture(self):
        from lyara.project import snapshot
        from lyara.liara import LiaraError
        self.cloud.register('healthy-app','02-node')
        self.cloud.register('crashing-app','06-startup-crash')
        artifact=snapshot(PROJECTS/'02-node',Path(self.temporary.name)/'artifacts')
        with self.cloud.client() as client:
            client.create('healthy-app','node','local-plan')
            client.create('crashing-app','node','local-plan')
            source=client.upload('healthy-app',artifact['path'])
            with self.assertRaises(LiaraError):
                client.release('crashing-app',source['sourceID'],'node',3000)
        self.assertEqual(self.cloud.process_count,0)

    def test_incomplete_static_archive_cannot_pass_as_named_fixture(self):
        from lyara.liara import LiaraError
        self.cloud.register('fixture-app','01-static')
        archive=Path(self.temporary.name)/'incomplete.tar.gz'
        with tarfile.open(archive,'w:gz') as output:
            output.add(PROJECTS/'01-static'/'liara.json',arcname='liara.json')
        with self.cloud.client() as client:
            client.create('fixture-app','static','local-plan')
            with self.assertRaises(LiaraError):
                client.upload('fixture-app',archive)
        self.assertEqual(self.cloud.process_count,0)

    def test_agent_evaluation_rejects_two_restarts_when_user_requested_one(self):
        from tests.lab import agent_checks
        class DuplicateRestartPlanner:
            name='deliberately-incorrect-test-provider'
            def plan(self,request,context):
                return Plan(summary='Incorrect duplicate restart',steps=[
                    {'action':'restart','title':'Restart','reason':'Requested','args':{}},
                    {'action':'restart','title':'Again','reason':'Unrequested','args':{}}])
        report=Path(self.temporary.name)/'duplicate-restart.json'
        code=agent_checks(DuplicateRestartPlanner(),report,'09-restart-approval')
        self.assertEqual(code,1)
        self.assertFalse(json.loads(report.read_text(encoding='utf8'))['cases'][0]['passed'])

    def test_complex_project_with_disabled_worker_fails_and_exposes_cause(self):
        fixture='08-worker-sqlite'
        self.cloud.register('fixture-app',fixture)
        plan=Plan(summary='Deploy with disabled worker to reproduce a fault',steps=[
            {'action':'create_app','title':'Create','reason':'Fixture setup','args':{'platform':'python','plan_id':'local-plan'}},
            {'action':'update_env','title':'Configure worker','reason':'Intentional fault',
             'args':{'env_refs':{'WORKER_ENABLED':'LYARA_DEPLOY_WORKER_ENABLED'}}},
            {'action':'deploy','title':'Deploy','reason':'Run fixture','args':{'platform':'python','port':3000}}])
        with patch.dict(os.environ,{'LYARA_DEPLOY_WORKER_ENABLED':'0'}):
            proposed=self.service.start(RunRequest(message='Deploy disabled worker fixture',intent='deploy',
                project='fixture-app',source=fixture,port=3000,plan_id='local-plan'),plan)
            result=self.service.approve(proposed['id'],proposed['plan_digest'],True)
        self.assertEqual(result['status'],'failed')
        self.assertIn('WorkerDisabled',self.cloud.logs('fixture-app'))


if __name__=='__main__':
    unittest.main()
