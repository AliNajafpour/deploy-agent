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


class NoviceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'package.json').write_text('{"scripts":{"start":"node server.mjs"}}')
        (self.root/'server.mjs').write_text('import http from "node:http"; http.createServer(()=>{}).listen(Number(process.env.PORT));')
        self.cloud=DemoCloud()
        self.seen={}
        owner=self
        class Planner:
            name='novice-check'
            def plan(inner,request,context):
                owner.seen.update(context)
                return Plan(summary='سایت را با تنظیمات مناسب منتشر می‌کنم.',steps=[{
                    'action':'deploy','title':'انتشار سایت','reason':'فایل شروع از PORT استفاده می‌کند؛ مقدار 3000 را برای اجرا تنظیم می‌کنم.',
                    'args':{'platform':'node','port':3000}}])
        self.service=Assistant(self.root,self.root/'.lyara',self.cloud.client(),Planner())

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def request(self,**overrides):
        return RunRequest.model_validate({'message':'سایتم را منتشر کن','intent':'deploy','project':'demo-app',**overrides})

    def test_model_resolves_missing_port_from_startup_files_without_asking_user(self):
        run=self.service.start(self.request())
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        self.assertEqual(run['context']['port'],3000)
        self.assertIn('process.env.PORT',self.seen['deployment_files']['server.mjs']['content'])
        self.assertEqual(run['plan']['questions'],[])
        self.assertEqual(self.cloud.mutations,[])
        final=self.service.approve(run['id'],run['plan_digest'],True)
        self.assertEqual(final['status'],'completed')

    def test_explicit_port_cannot_be_overridden_by_model(self):
        run=self.service.start(self.request(port=8080))
        self.assertEqual(run['status'],'failed')
        self.assertEqual(self.cloud.mutations,[])

    def test_model_can_resolve_unknown_platform_from_bounded_backend_source(self):
        (self.root/'package.json').unlink()
        self.cloud.apps['demo-app'].pop('type')
        run=self.service.start(self.request())
        self.assertEqual(run['status'],'awaiting_approval',run.get('error'))
        self.assertEqual(run['context']['platform'],'node')
        self.assertEqual(run['context']['questions'],[])

    def test_source_secrets_are_omitted_from_the_planner_packet(self):
        (self.root/'app.py').write_text('app.config["SECRET_KEY"] = "never-send-startup-secret"')
        self.service.start(self.request())
        self.assertNotIn('never-send-startup-secret',json.dumps(self.seen))
        self.assertIn('app.py',self.seen['omitted_files'])

    def test_missing_hosting_choice_is_a_cost_question_not_a_plan_id_question(self):
        run=self.service.start(self.request(project='new-site'))
        self.assertEqual(run['status'],'needs_input')
        text=' '.join(run['plan']['questions'])
        self.assertNotIn('شناسه',text)
        self.assertNotIn('پورت',text)
        self.assertIn('هزینه',text)
        self.assertEqual(self.cloud.mutations,[])

    def test_hosting_options_are_operator_provided_and_costs_not_invented(self):
        option={'id':'verified-plan','title':'میزبانی شروع','cost':'هزینهٔ اعلام‌شده در حساب'}
        with patch.dict('os.environ',{'LYARA_HOSTING_OPTIONS':json.dumps([option])}):
            run=self.service.start(self.request(project='new-site'))
        self.assertEqual(run['context']['hosting_options'],[option])
        self.assertEqual(run['status'],'needs_input')

    def test_invalid_or_duplicate_hosting_options_are_rejected(self):
        for value in ['{"id":"x"}', '[{"id":"x","title":"شروع"}]',
                      '[{"id":"x","title":"شروع","cost":"واقعی"},{"id":"x","title":"دیگر","cost":"واقعی"}]']:
            with patch.dict('os.environ',{'LYARA_HOSTING_OPTIONS':value}):
                run=self.service.start(self.request())
            self.assertEqual(run['status'],'failed')
        self.assertEqual(self.cloud.mutations,[])

    def test_approval_names_shutdown_direction_and_target_address(self):
        from lyara.presentation import change_review
        plan=Plan(summary='Requested changes',steps=[
            {'action':'scale','title':'Stop','reason':'Requested','args':{'scale':0}},
            {'action':'attach_domain','title':'Connect','reason':'Requested','args':{'domain':'my-site.example.com'}}])
        review=change_review({'project':'my-site'},plan,{})
        self.assertEqual(review['changes'][0]['impact'],'خاموش‌کردن سایت')
        self.assertIn('my-site.example.com',review['changes'][1]['impact'])

    def test_approval_includes_selected_hosting_title_and_cost(self):
        option={'id':'chosen','title':'گزینهٔ من','cost':'هزینهٔ ماهانهٔ اعلام‌شده'}
        plan=Plan(summary='Requested changes',steps=[{
            'action':'resize','title':'Resize','reason':'Requested','args':{'plan_id':'chosen'}}])
        with patch.dict('os.environ',{'LYARA_HOSTING_OPTIONS':json.dumps([option])}):
            run=self.service.start(RunRequest(message='Change hosting after approval',project='demo-app',intent='operate'),plan)
        self.assertEqual(run['status'],'awaiting_approval')
        self.assertIn('گزینهٔ من',run['review']['changes'][0]['impact'])
        self.assertIn('هزینهٔ ماهانهٔ اعلام‌شده',run['review']['changes'][0]['impact'])
        self.assertEqual(self.cloud.mutations,[])

    def test_source_picker_excludes_private_and_dependency_directories(self):
        for name in ['site-one','.lyara','node_modules','private-state']:
            p=self.root/name
            p.mkdir(exist_ok=True)
            (p/'index.html').write_text('example')
        # The configured data tree is excluded even when it has a nonstandard name.
        other=Assistant(self.root,self.root/'private-state',self.cloud.client(),None)
        try:
            with TestClient(create_app(other,access_token='access',allowed_hosts={'testserver'})) as client:
                self.assertEqual(client.get('/api/sources').status_code,401)
                r=client.get('/api/sources',headers={'Authorization':'Bearer access'})
                self.assertEqual(r.status_code,200)
                names={item['source'] for item in r.json()}
                self.assertIn('site-one',names)
                self.assertTrue(names.isdisjoint({'.lyara','node_modules','private-state'}))
        finally:
            other.close()


if __name__=='__main__':
    unittest.main()
