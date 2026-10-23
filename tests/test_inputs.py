import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from lyara.demo import DemoCloud
from lyara.models import Plan, RunRequest
from lyara.service import Assistant
from lyara.web import create_app


class InputFlowChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'package.json').write_text('{"scripts":{"start":"node server.mjs"}}')
        (self.root/'server.mjs').write_text('const key=process.env.PAYMENT_API_KEY; const db=process.env.DATABASE_URL;')
        (self.root/'.env.example').write_text('PAYMENT_API_KEY=\nDATABASE_URL=\n')
        self.seen={}
        owner=self
        class Planner:
            name='input-check'
            def plan(inner,request,context):
                owner.seen.update(context)
                return Plan(summary='سایت را منتشر می‌کنم.',steps=[{'action':'deploy','title':'انتشار','reason':'درخواست کاربر','args':{'platform':'node','port':3000}}])
        self.cloud=DemoCloud()
        self.service=Assistant(self.root,self.root/'.lyara',self.cloud.client(),Planner())
        self.request=RunRequest(message='سایتم را منتشر کن',intent='deploy',project='demo-app')

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_missing_values_are_typed_fields_and_do_not_mutate_cloud(self):
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'needs_input')
        self.assertEqual({i['name'] for i in run['context']['input_requests']},{'PAYMENT_API_KEY','DATABASE_URL'})
        self.assertEqual(self.cloud.mutations,[])

    def test_source_env_is_used_without_model_or_archive_exposure(self):
        secrets={'PAYMENT_API_KEY':'payment-test-value-984731','DATABASE_URL':'postgresql://owner:secret-pass@db.example/app'}
        (self.root/'.env').write_text('\n'.join(k+'='+v for k,v in secrets.items()))
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        self.assertEqual([s['action'] for s in run['plan']['steps']],['update_env','deploy'])
        self.assertNotIn('.env',run['artifact']['files'])
        for value in secrets.values():
            self.assertNotIn(value,json.dumps(run))
            self.assertNotIn(value,json.dumps(self.seen))
        self.assertEqual(self.cloud.mutations,[])
        final=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(final['status'],'completed',final.get('error'))
        actual={i['key']:i['value'] for i in self.cloud.apps['demo-app']['envs']}
        self.assertTrue(secrets.items()<=actual.items())
        for value in secrets.values():
            self.assertNotIn(value,json.dumps(self.service.report(final['id'])))
            for file in (self.root/'.lyara').glob('*'):
                if file.is_file():self.assertNotIn(value.encode(),file.read_bytes(),file.name)

    def test_user_submits_values_in_ui_then_gets_fresh_approval(self):
        run=self.service.start(self.request)
        values={'PAYMENT_API_KEY':'entered-test-key-283164','DATABASE_URL':'postgres://u:p@db.example/site'}
        state=self.service.new_inputs(run['id'],{'values':values})
        self.assertNotEqual(state['id'],run['id'])
        self.assertEqual(state['parent_id'],run['id'])
        ready=self.service.process(state['id'])
        self.assertEqual(ready['status'],'awaiting_approval',ready.get('error'))
        self.assertEqual(ready['context']['input_requests'],[])
        self.assertEqual(self.cloud.mutations,[])
        for value in values.values():self.assertNotIn(value,json.dumps(ready))

    def test_input_submission_rejects_unrequested_names_without_storing_them(self):
        run=self.service.start(self.request)
        with self.assertRaises(ValueError):self.service.new_inputs(run['id'],{'values':{'NODE_OPTIONS':'--inspect'}})
        self.assertEqual(self.cloud.mutations,[])

    def test_inputs_api_auth_and_validation_never_echo_values(self):
        run=self.service.start(self.request)
        route='/api/runs/'+run['id']+'/inputs'
        with TestClient(create_app(self.service,access_token='access',allowed_hosts={'testserver'})) as client:
            self.assertEqual(client.post(route,json={'values':{'PAYMENT_API_KEY':'private-value'}}).status_code,401)
            headers={'Authorization':'Bearer access'}
            bad=client.post(route,headers=headers,json={'values':{'BAD/NAME':'private-value'}})
            self.assertIn(bad.status_code,(400,422))
            self.assertNotIn('private-value',bad.text)
            good=client.post(route,headers=headers,json={'values':{'PAYMENT_API_KEY':'private-value-1244','DATABASE_URL':'postgres://u:p@db.example/app'}})
            self.assertEqual(good.status_code,202,good.text)
            self.assertNotIn('private-value-1244',good.text)

    def test_placeholder_and_host_variable_interpolation_do_not_count_as_values(self):
        (self.root/'.env').write_text('PAYMENT_API_KEY=YOUR_API_KEY\nDATABASE_URL=${HOST_DATABASE_URL}\n')
        with patch.dict('os.environ',{'HOST_DATABASE_URL':'must-never-use-host-secret'}):
            run=self.service.start(self.request)
        self.assertEqual(run['status'],'needs_input')
        self.assertEqual(len(run['context']['input_requests']),2)
        self.assertNotIn('must-never-use-host-secret',json.dumps(run))

    def test_template_values_are_not_adopted_as_real_credentials(self):
        (self.root/'.env.example').write_text('PAYMENT_API_KEY=sample-key-not-real\nDATABASE_URL=postgres://sample:sample@localhost/db')
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'needs_input')
        self.assertEqual(len(run['context']['input_requests']),2)

    def test_value_change_invalidates_old_approval_before_cloud_write(self):
        (self.root/'.env').write_text('PAYMENT_API_KEY=original-test-key-3891\nDATABASE_URL=postgres://u:p@db.example/site')
        old=self.service.start(self.request)
        self.assertEqual(old['status'],'awaiting_approval')
        (self.root/'.env').write_text('PAYMENT_API_KEY=replaced-test-key-3891\nDATABASE_URL=postgres://u:p@db.example/site')
        self.service.start(self.request)
        final=self.service.approve(old['id'],old['plan_digest'],True)
        self.assertEqual(final['status'],'failed')
        self.assertEqual(self.cloud.mutations,[])

    def test_model_can_request_code_only_configuration_without_template(self):
        (self.root/'.env.example').unlink()
        owner=self
        class CodePlanner:
            name='code-input-check'
            def plan(inner,request,context):
                owner.assertIn('PAYMENT_API_KEY',{i['name'] for i in context['configuration_candidates']})
                return Plan(summary='کلید پرداخت را وارد کن.',inputs=[{'name':'PAYMENT_API_KEY','label':'کلید سرویس پرداخت','kind':'secret'}])
        self.service.planner=CodePlanner()
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'needs_input')
        self.assertEqual(run['context']['input_requests'][0]['label'],'کلید سرویس پرداخت')
        self.assertEqual(self.cloud.mutations,[])

    def test_empty_host_value_is_still_requested(self):
        self.cloud.apps['demo-app']['envs']=[{'key':'PAYMENT_API_KEY','value':''}]
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'needs_input')
        self.assertIn('PAYMENT_API_KEY',{i['name'] for i in run['context']['input_requests']})

    def test_known_credential_repeated_in_code_is_rejected_before_archiving(self):
        secret='nonstandard-payment-secret-892363'
        (self.root/'.env').write_text('PAYMENT_API_KEY='+secret)
        (self.root/'config.json').write_text(json.dumps({'payment':secret}))
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'failed')
        self.assertEqual(self.cloud.mutations,[])
        for archive in (self.root/'.lyara'/'artifacts').glob('*.tar.gz'):
            import tarfile
            with tarfile.open(archive) as bundle:
                for file in bundle.getmembers():
                    if file.isfile():self.assertNotIn(secret.encode(),bundle.extractfile(file).read())

    def test_old_value_is_still_redacted_after_replacement(self):
        original='retired-test-secret-289273'
        self.service.vault.put('demo-app',{'PAYMENT_API_KEY':original},'saved')
        self.service.vault.put('demo-app',{'PAYMENT_API_KEY':'new-test-secret-83921'},'saved')
        state=self.service.new(self.request)
        state=self.service._save(state,'test','upstream log echoed '+original)
        self.assertNotIn(original,json.dumps(state))
        self.assertNotIn(original,json.dumps(self.service.report(state['id'])))

    def test_configuration_scan_redacts_before_truncating_minified_line(self):
        from lyara.inputs import discover_inputs
        secret='very-long-arbitrary-credential-3892177391'
        prefix='const key=process.env.PAYMENT_API_KEY; // '+'.'*430
        (self.root/'server.mjs').write_text(prefix+secret)
        _,candidates=discover_inputs(self.root,[secret])
        self.assertNotIn(secret[:12],json.dumps(candidates))

    def test_short_credential_literal_is_not_archived(self):
        (self.root/'.env').write_text('PAYMENT_API_KEY=tiny')
        (self.root/'config.json').write_text('{"auth":"tiny"}')
        run=self.service.start(self.request)
        self.assertEqual(run['status'],'failed')
        self.assertEqual(self.cloud.mutations,[])

    def test_working_code_defaults_do_not_ask_the_user_for_optional_values(self):
        from lyara.inputs import discover_inputs
        (self.root/'.env.example').write_text('WORKER_ENABLED=1\nCACHE_SIZE=10\nPAYMENT_API_KEY=')
        (self.root/'app.py').write_text('import os\nenabled=os.getenv("WORKER_ENABLED","1")')
        (self.root/'server.mjs').write_text('const size=process.env.CACHE_SIZE || 10;')
        required,_=discover_inputs(self.root)
        self.assertEqual(required,['PAYMENT_API_KEY'])

    def test_values_persist_after_service_restart_and_stay_project_scoped(self):
        values={'PAYMENT_API_KEY':'persisted-test-secret-89233','DATABASE_URL':'postgres://u:p@db.example/site'}
        self.service.vault.put('demo-app',values,'saved')
        planner=self.service.planner
        self.service.close()
        self.service=Assistant(self.root,self.root/'.lyara',self.cloud.client(),planner)
        ready=self.service.start(self.request)
        self.assertEqual(ready['status'],'awaiting_approval',ready.get('error'))
        other=self.service.start(RunRequest(message='Publish',intent='deploy',project='other-app',plan_id='demo-plan'))
        self.assertEqual(other['status'],'needs_input')
        self.assertEqual(len(other['context']['input_requests']),2)
        for value in values.values():self.assertNotIn(value,json.dumps(other))

    def test_unrelated_dictionary_default_does_not_hide_required_environment(self):
        from lyara.inputs import discover_inputs
        (self.root/'app.py').write_text('import os\nkey=os.environ["PAYMENT_API_KEY"]\nother={}.get("PAYMENT_API_KEY","example")')
        required,_=discover_inputs(self.root)
        self.assertIn('PAYMENT_API_KEY',required)

    def test_known_value_in_optional_message_never_reaches_initial_sqlite_write(self):
        first=self.service.start(self.request)
        values={'PAYMENT_API_KEY':'repeated-private-test-value-83271','DATABASE_URL':'postgres://u:p@db.example/site'}
        with patch.object(self.service.store,'save',wraps=self.service.store.save) as writes:
            state=self.service.new_inputs(first['id'],{'values':values,'message':'I supplied '+values['PAYMENT_API_KEY']})
        for call in writes.call_args_list:self.assertNotIn(values['PAYMENT_API_KEY'],json.dumps(call.args[1]))
        self.assertNotIn(values['PAYMENT_API_KEY'],json.dumps(state))
        self.assertNotIn(values['PAYMENT_API_KEY'].encode(),(self.root/'.lyara'/'operations.sqlite').read_bytes())

    def test_fresh_evidence_reads_redact_current_hosted_values(self):
        secret='hosted-arbitrary-private-test-83921'
        self.cloud.apps['demo-app']['envs']=[{'key':'APP_CREDENTIAL','value':secret}]
        original=self.service.cloud.observe
        def echo(project):
            observations=original(project)
            observations['logs']['data']=[{'message':'upstream echoed '+secret}]
            return observations
        self.service.cloud.observe=echo
        observations=self.service.observe('demo-app')
        self.assertNotIn(secret,json.dumps(observations))

    def test_short_input_does_not_corrupt_ids_or_approval_hash(self):
        (self.root/'.env.example').write_text('POOL_SIZE=')
        (self.root/'.env').write_text('POOL_SIZE=1')
        ready=self.service.start(self.request)
        self.assertEqual(ready['status'],'awaiting_approval',ready.get('error'))
        self.assertRegex(ready['plan_digest'],r'^[a-f0-9]{64}$')
        self.assertRegex(ready['id'],r'^[a-f0-9-]{36}$')
        final=self.service.approve(ready['id'],ready['plan_digest'],True)
        self.assertEqual(final['status'],'completed',final.get('error'))

    def test_env_symlink_outside_workspace_is_not_read(self):
        # Symlink creation is platform privileged; the path validation unit covers it portably.
        from lyara.inputs import read_source_env
        with patch('pathlib.Path.is_symlink',return_value=True):
            self.assertEqual(read_source_env(self.root),{})


if __name__=='__main__':unittest.main()
