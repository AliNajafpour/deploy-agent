import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from lyara.demo import DemoCloud, DemoPlanner
from lyara.service import Assistant
from lyara.web import create_app


class WebChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'index.html').write_text('<p>test</p>')
        self.service = Assistant(root, root / '.lyara', DemoCloud().client(), DemoPlanner())
        self.client = TestClient(create_app(self.service, access_token='web-test-secret', allowed_hosts={'testserver'}))
        self.headers = {'Authorization': 'Bearer web-test-secret'}

    def tearDown(self):
        self.client.close()
        self.service.close()
        self.tmp.cleanup()

    def test_read_and_write_apis_require_authentication(self):
        self.assertEqual(self.client.get('/api/projects').status_code, 401)
        self.assertEqual(self.client.post('/api/runs', json={}).status_code, 401)
        self.assertEqual(self.client.get('/api/config', headers=self.headers).status_code, 200)

    def test_cross_site_and_rebound_host_requests_are_rejected(self):
        response = self.client.post('/api/runs', headers={**self.headers, 'Origin': 'https://evil.example'}, json={})
        self.assertEqual(response.status_code, 403)
        response = self.client.get('/api/config', headers={**self.headers, 'Host': 'evil.example'})
        self.assertEqual(response.status_code, 403)

    def test_ui_has_accessible_persian_entry_point(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('lang="fa"', response.text)
        self.assertIn('dir="rtl"', response.text)
        self.assertIn('Content-Security-Policy', response.headers)

    def test_web_can_complete_deployment_with_explicit_approval(self):
        response = self.client.post('/api/runs', headers=self.headers, json={
            'message': 'Deploy test', 'intent': 'deploy', 'project': 'new-app',
            'platform': 'static', 'port': 80, 'plan_id': 'demo-plan'})
        self.assertEqual(response.status_code, 202)
        run_id = response.json()['id']
        run = self.wait_for(run_id, {'awaiting_approval'})
        result = self.client.post('/api/runs/' + run_id + '/approval', headers=self.headers,
            json={'approved': True, 'plan_digest': run['plan_digest']})
        self.assertEqual(result.status_code, 202)
        self.assertEqual(self.wait_for(run_id, {'completed'})['status'], 'completed')

    def wait_for(self, run_id, statuses):
        for _ in range(100):
            result = self.client.get('/api/runs/' + run_id, headers=self.headers).json()
            if result['status'] in statuses:
                return result
            time.sleep(.01)
        self.fail('Run did not reach expected state: ' + str(result))


if __name__ == '__main__':
    unittest.main()
