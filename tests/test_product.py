import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from lyara.demo import DemoCloud, DemoPlanner
from lyara.models import Plan, RunRequest
from lyara.service import Assistant
from lyara.web import create_app
from lyara.liara import LiaraError


class ProductChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'index.html').write_text('presentation test')
        self.cloud=DemoCloud()
        self.service=Assistant(self.root,self.root/'.lyara',self.cloud.client(),DemoPlanner())

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def deploy(self,port=80):
        return self.service.start(RunRequest(message='Deploy this source',intent='deploy',
            project='new-app',plan_id='demo-plan',port=port,platform='static'))

    def test_answer_preserves_original_target_and_requires_new_approval(self):
        before=self.deploy(port=None)
        self.assertEqual(before['status'],'needs_input')
        run=self.service.answer(before['id'],{'message':'Use port 8080','port':8080})
        self.assertEqual(run['status'],'awaiting_approval')
        self.assertNotEqual(run['id'],before['id'])
        self.assertEqual(run['parent_id'],before['id'])
        self.assertEqual(run['request']['project'],'new-app')
        self.assertEqual(run['request']['port'],8080)
        self.assertIn('Deploy this source',run['request']['message'])
        self.assertIn('Use port 8080',run['request']['message'])
        self.assertEqual(self.cloud.mutations,[])
        self.assertEqual(self.service.get(before['id'])['status'],'needs_input')

    def test_answer_cannot_retarget_or_change_an_already_approved_run(self):
        before=self.deploy(port=None)
        with self.assertRaises(ValueError):
            self.service.answer(before['id'],{'message':'switch target','project':'other-app','port':80})
        ready=self.deploy()
        with self.assertRaises(ValueError):
            self.service.answer(ready['id'],{'message':'replace an approved plan','port':8080})
        self.assertEqual(self.cloud.mutations,[])

    def test_review_explains_impacts_and_does_not_invent_price_or_recovery(self):
        run=self.deploy()
        review=run['review']
        self.assertEqual(review['project'],'new-app')
        self.assertEqual([item['action'] for item in review['changes']],['create_app','deploy'])
        self.assertTrue(all(item['impact'] and item['risk'] and item['recovery'] for item in review['changes']))
        self.assertIsNone(review['previous_source'])

    def test_report_has_approval_audit_and_omits_private_paths_and_secret_metadata(self):
        run=self.deploy()
        run=self.service.approve(run['id'],run['plan_digest'],True)
        report=self.service.report(run['id'])
        self.assertEqual(report['status'],'completed')
        self.assertEqual(report['approval']['plan_digest'],run['plan_digest'])
        self.assertTrue(report['approval']['approved'])
        self.assertEqual(len(report['actions']),3)
        self.assertTrue(all(item['status']=='succeeded' for item in report['actions']))
        self.assertEqual(report['after']['health']['status'],200)
        text=json.dumps(report)
        self.assertNotIn(str(self.root),text)
        self.assertNotIn('env_fingerprints',text)
        self.assertNotIn('demo-db-password',text)

    def test_provider_echo_of_known_cloud_token_cannot_escape_via_report_or_journal(self):
        original=self.service.cloud.create
        def echo(*args):
            return {**original(*args),'message':'provider echoed '+self.service.cloud.token}
        run=self.deploy()
        with patch.object(self.service.cloud,'create',side_effect=echo):
            run=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertNotIn(self.service.cloud.token,json.dumps(self.service.report(run['id'])))
        self.assertNotIn(self.service.cloud.token,json.dumps(self.service.store.actions(run['id'])))
        self.service.store.write_once(run['id']+':legacy',lambda:{'message':self.service.cloud.token})
        self.assertNotIn(self.service.cloud.token,json.dumps(self.service.report(run['id'])))

    def test_web_answer_and_report_require_auth_and_preserve_request(self):
        before=self.deploy(port=None)
        with TestClient(create_app(self.service,access_token='test-access',allowed_hosts={'testserver'})) as client:
            route='/api/runs/'+before['id']+'/answer'
            self.assertEqual(client.post(route,json={'message':'8080','port':8080}).status_code,401)
            headers={'Authorization':'Bearer test-access'}
            response=client.post(route,headers=headers,json={'message':'Use 8080','port':8080})
            self.assertEqual(response.status_code,202)
            child=response.json()
            self.assertEqual(child['parent_id'],before['id'])
            report_route='/api/runs/'+before['id']+'/report'
            self.assertEqual(client.get(report_route).status_code,401)
            result=client.get(report_route,headers=headers)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['id'],before['id'])
            markdown=client.get(report_route+'?format=markdown',headers=headers)
            self.assertEqual(markdown.status_code,200)
            self.assertIn('attachment',markdown.headers['content-disposition'])
            self.assertIn('new-app',markdown.text)
            self.assertEqual(client.get(report_route+'?format=html',headers=headers).status_code,400)

    def test_recovery_requires_a_known_prior_source_and_new_approval(self):
        run=self.deploy()
        run=self.service.approve(run['id'],run['plan_digest'],True)
        with self.assertRaises(ValueError):
            self.service.recovery(run['id'])
        self.assertEqual(self.cloud.mutations[-1][1],'/v2/projects/new-app/releases')

    def test_prior_source_can_be_recovered_from_own_history_when_api_omits_it(self):
        first=self.deploy()
        first=self.service.approve(first['id'],first['plan_digest'],True)
        # The documented release listing need not contain sourceID; our upload journal does.
        again=self.deploy()
        self.assertEqual(again['review']['previous_source']['source_id'],first['results'][1]['sourceID'])

    def test_default_health_is_available_to_the_planner_before_diagnosis(self):
        captured={}
        class Planner:
            def plan(self,request,context):
                captured.update(context)
                return Plan(summary='Inspect only')
        self.service.planner=Planner()
        self.service.start(RunRequest(message='Why is my site slow?',project='demo-app'))
        self.assertEqual(captured['health']['id'],'before.health')
        self.assertEqual(captured['health']['status'],200)

    def test_recovery_keeps_previous_platform_when_new_deploy_changes_it(self):
        self.cloud.releases_by_app['demo-app']=[{'_id':'old-release','sourceID':'old-source','state':'READY','port':3000}]
        run=self.service.start(RunRequest(message='Publish Python',intent='deploy',project='demo-app',platform='python',port=8000))
        self.assertEqual(run['review']['previous_source']['platform'],'node')

    def test_source_history_uses_executed_step_parameters_not_request_context(self):
        self.service.store.save('recovery-example',{'id':'recovery-example','request':{'project':'demo-app'},
            'context':{'platform':'python','port':8000},'results':[{'sourceID':'old-node-source','releaseID':'recovery-release'}],
            'plan':{'steps':[{'action':'redeploy','args':{'source_id':'old-node-source','platform':'node','port':3000}}]}})
        known=self.service.store.known_release('demo-app','recovery-release')
        self.assertEqual(known,{'sourceID':'old-node-source','platform':'node','port':3000})

    def test_restart_acknowledgement_does_not_prove_restart_even_with_http200(self):
        plan=Plan(summary='Restart',steps=[{'action':'restart','title':'Restart','reason':'Requested','args':{}}])
        run=self.service.start(RunRequest(message='Restart',intent='operate',project='demo-app'),plan)
        run=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertTrue(run['verification']['health']['healthy'])
        self.assertEqual(run['status'],'needs_attention')
        self.assertIsNone(run['verification']['action_checks'][0]['matched'])

    def test_dns_zone_requires_readback_not_only_healthy_website(self):
        plan=Plan(summary='Create DNS zone',steps=[{'action':'create_zone','title':'DNS','reason':'Requested','args':{'zone':'example.com'}}])
        run=self.service.start(RunRequest(message='DNS',intent='operate',project='demo-app'),plan)
        run=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(run['status'],'needs_attention')
        self.assertFalse(run['verification']['action_checks'][0]['matched'])

    def test_known_release_timeout_can_be_rechecked_without_repeating_a_write(self):
        run=self.deploy()
        with patch.object(self.service.cloud,'wait_release',side_effect=LiaraError(408,'deadline',uncertain=True)):
            run=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(run['status'],'unknown')
        self.assertTrue(run['pending_release']['releaseID'])
        mutations=list(self.cloud.mutations)
        result=self.service.recheck(run['id'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(self.cloud.mutations,mutations)
        self.assertIsNone(result['pending_release'])

    def test_recheck_never_executes_remaining_domain_step(self):
        run=self.service.start(RunRequest(message='Deploy and attach domain',intent='deploy',project='new-app',
            plan_id='demo-plan',port=80,platform='static',domain='www.example.com'))
        with patch.object(self.service.cloud,'wait_release',side_effect=LiaraError(408,'deadline',uncertain=True)):
            run=self.service.approve(run['id'],run['plan_digest'],True)
        mutations=list(self.cloud.mutations)
        run=self.service.recheck(run['id'])
        self.assertEqual(self.cloud.mutations,mutations)
        self.assertEqual(run['status'],'needs_attention')
        self.assertFalse(any(path=='/v1/domains' for _,path in self.cloud.mutations))


if __name__=='__main__':
    unittest.main()
