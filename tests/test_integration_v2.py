import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from lyara.demo import DemoCloud
from lyara.models import Plan
from lyara.project import snapshot
from lyara.service import Assistant
from tests.test_hosting_v2 import fixture


class AccountCloud(DemoCloud):
    def __init__(self):
        super().__init__()
        self.catalog = fixture()
        self.catalog['networks']={'networks':[{'_id':'network-a','name':'Existing connection'}]}
        self.created=[]

    def client(self):
        client=super().client()
        client.sandbox=False
        return client

    def handle(self,request):
        path=request.url.path
        if path=='/v1/me':
            return httpx.Response(200,json={**self.catalog['account'],'nationalCode':'private-identity',
                'storage':{'accessKey':'private-storage-key','secretKey':'private-storage-secret'}})
        if path=='/v1/billing':return httpx.Response(200,json=self.catalog['billing']['data'])
        if path=='/v1/networks':
            if request.method=='POST':
                self.mutations.append((request.method,path))
                name=json.loads(request.content)['networkName']
                self.catalog['networks']['networks'].append({'_id':'created-network','name':name})
                return httpx.Response(201,json={})
            return httpx.Response(200,json=self.catalog['networks'])
        if path.startswith('/v2/releases/') and path.endswith('/build-logs'):
            return httpx.Response(200,json={'release':{'state':'FAILED' if self.fail_release else 'READY'},
                'buildOutput':[{'line':'ModuleNotFoundError: No module named requests; password=private-build-key',
                                'stream':'stderr','createdAt':'2026-10-23T12:00:00Z'}]})
        if path=='/v1/projects' and request.method=='POST':
            body=json.loads(request.content);self.created.append(body)
            response=super().handle(request)
            self.apps[body['name']].update(bundlePlanID=body.get('bundlePlanID'),network=body.get('network'))
            return response
        return super().handle(request)


class Planner:
    name='integration-test'
    def plan(self,request,context):
        steps=[]
        if not context['exists']:
            steps.append({'action':'create_app','title':'Create','reason':'Chosen hosting',
                'args':{'platform':'static','plan_id':request['plan_id']}})
        steps.append({'action':'deploy','title':'Deploy','reason':'Reviewed source','args':{'platform':'static','port':80}})
        return Plan(summary='Publish the selected source.',steps=steps)


class IntegrationV2Checks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'index.html').write_text('<h1>Site</h1>')
        self.cloud=AccountCloud()
        self.service=Assistant(self.root,self.root/'state',self.cloud.client(),Planner())

    def tearDown(self):
        self.service.close();self.tmp.cleanup()

    def proposal(self,project='new-app'):
        return self.service.start({'project':project,'intent':'deploy','message':'Publish','plan_id':'test-small',
            'bundle_plan_id':'test-bronze','network':'network-a','port':80})

    def test_live_catalog_privacy_and_exact_bundle_network_body(self):
        catalog=self.service.hosting_catalog()
        serialized=json.dumps(catalog)
        self.assertNotIn('private-identity',serialized);self.assertNotIn('private-storage',serialized)
        run=self.proposal()
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        self.assertEqual(self.cloud.mutations,[])
        result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual(self.cloud.created[0],{'name':'new-app','platform':'static','planID':'test-small',
            'bundlePlanID':'test-bronze','network':'network-a'})
        self.assertTrue(result['verification']['stability']['stable'])
        self.assertEqual(result['verification']['samples']['summary']['success'],6)
        self.assertIn('new-app',self.service.controls()['managed_projects'])

    def test_network_creation_is_reviewed_and_never_inferred_response_id(self):
        self.cloud.catalog['networks']={'networks':[]}
        run=self.service.start({'project':'new-app','intent':'deploy','message':'Publish','plan_id':'test-small',
            'bundle_plan_id':'test-bronze','port':80})
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        self.assertEqual(run['plan']['steps'][0]['action'],'create_network')
        self.assertEqual(self.cloud.mutations,[])
        result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual(self.cloud.created[0]['network'],'created-network')
        self.assertEqual(sum(path=='/v1/networks' for _,path in self.cloud.mutations),1)

    def test_scope_is_readonly_and_stop_off_cannot_restore_approval(self):
        readonly=self.proposal('demo-app')
        self.assertEqual(readonly['status'],'needs_input')
        self.assertEqual(self.cloud.mutations,[])
        run=self.proposal()
        controls=self.service.controls()
        stopped=self.service.emergency(True,controls['generation'])
        self.service.emergency(False,stopped['generation'])
        self.assertEqual(self.service.get(run['id'])['status'],'invalidated')
        self.assertEqual(self.service.approve(run['id'],run['plan_digest'],True)['status'],'invalidated')
        self.assertEqual(self.cloud.mutations,[])
        self.service.resume(run['id'])
        self.assertEqual(self.cloud.mutations,[])

    def test_stop_between_upload_and_release_keeps_actual_journal_result(self):
        run=self.proposal()
        original=self.service.cloud.upload
        def upload(*args):
            result=original(*args)
            controls=self.service.controls();self.service.emergency(True,controls['generation'])
            return result
        with patch.object(self.service.cloud,'upload',side_effect=upload):
            result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(any(path.endswith('/sources') for _,path in self.cloud.mutations))
        self.assertFalse(any(path.endswith('/releases') for _,path in self.cloud.mutations))
        self.assertTrue(any(a['status']=='succeeded' and a['id'].endswith(':upload') for a in self.service.store.actions(run['id'])))

    def test_usage_survives_redaction_and_planner_failure(self):
        class UsagePlanner:
            name='usage-test'
            def plan(self,*args):raise RuntimeError('Temporary model failure')
            def drain_usage(self):return [{'model':'luna','input_tokens':42,'output_tokens':7,
                'total_tokens':49,'cost_usd':None,'cost_kind':'unknown'}]
        self.service.planner=UsagePlanner()
        run=self.proposal()
        self.assertEqual(run['status'],'failed')
        usage=self.service.report(run['id'])['model_usage'][0]
        self.assertEqual((usage['input_tokens'],usage['output_tokens'],usage['total_tokens']),(42,7,49))
        self.assertIsNone(usage['cost_usd'])

    def test_stop_during_env_read_blocks_following_post(self):
        self.service.manage('demo-app',True,self.service.controls()['generation'])
        plan=Plan(summary='Update settings',steps=[{'action':'update_env','title':'Update',
            'reason':'Requested','args':{'env_refs':{'DB_PASSWORD':'LYARA_DEPLOY_DB_PASSWORD'}}}])
        with patch.dict(os.environ,{'LYARA_DEPLOY_DB_PASSWORD':'private-test-value'}):
            run=self.service.start({'project':'demo-app','intent':'operate','message':'Update'},plan)
            self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
            original=self.service.cloud.project
            def read_then_stop(*args,**kwargs):
                result=original(*args,**kwargs)
                if kwargs.get('raw') and not self.service.controls()['stopped']:
                    self.service.emergency(True,self.service.controls()['generation'])
                return result
            with patch.object(self.service.cloud,'project',side_effect=read_then_stop):
                result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(any(path=='/v1/projects/update-envs' for _,path in self.cloud.mutations))

    def test_failed_build_is_redacted_and_carried_into_repair(self):
        self.cloud.fail_release=True
        run=self.proposal();result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'failed')
        self.assertIn('ModuleNotFoundError',json.dumps(result['build_progress']))
        self.assertNotIn('private-build-key',json.dumps(result))
        following=self.service.new_repair(run['id'])
        prepared=self.service._prepare(following)
        self.assertIn('build_logs',prepared['context']['observations'])
        self.assertIn('dependency',json.dumps(prepared['context']['diagnostics']).lower())

    def test_resize_rechecks_actual_target_quote_and_preserves_old_outcome(self):
        self.cloud.apps['demo-app']['bundlePlanID']='test-standard'
        self.service.manage('demo-app',True,self.service.controls()['generation'])
        run=self.service.start({'project':'demo-app','intent':'operate','message':'Review larger hosting','plan_id':'demo-plan'},
            Plan(summary='Review capacity',steps=[{'action':'resize','title':'Resize','reason':'Operator review',
                'args':{'plan_id':'test-large'}}]))
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        quote=run['context']['billing_quotes']['test-large']
        self.assertIn(quote['cost'],run['review']['changes'][0]['impact'])
        self.cloud.catalog['account']['plans']['projects']['test-large']['price']=999999
        result=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(any(path.endswith('/resize') for _,path in self.cloud.mutations))

    def test_unsafe_source_does_not_trigger_prepare_verify_or_monitor_website_requests(self):
        (self.root/'index.html').unlink()
        (self.root/'app.js').write_text("app.get('/', (req,res)=>{counter++;res.json({counter});});")
        artifact=snapshot(self.root,self.service.data_dir/'artifacts',excluded_roots=(self.service.data_dir,))
        self.service.store.save('old-run',{'id':'old-run','request':{'project':'demo-app','intent':'deploy','source':'.'},
            'status':'completed','plan':{'steps':[{'action':'deploy','args':{'platform':'node','port':3000}}]},
            'artifact':artifact,'results':[{'releaseID':'previous-release','sourceID':'source','release':{'verified':True}}]})
        self.service.planner=None
        with patch.object(self.service.cloud,'health',wraps=self.service.cloud.health) as health:
            run=self.service.start({'project':'demo-app','intent':'diagnose','message':'Check safely'})
            self.assertEqual(run['status'],'observed',run.get('error'))
            self.assertEqual(run['context']['probe_targets'],[])
            result=self.service.monitor_probe('demo-app',self.service.observe('demo-app'))
            self.assertFalse(result['probe_attempted'])
            health.assert_not_called()
        self.assertEqual(self.cloud.mutations,[])


if __name__=='__main__':unittest.main()
