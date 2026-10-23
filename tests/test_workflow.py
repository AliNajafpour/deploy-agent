import json
import tempfile
import unittest
from pathlib import Path

from lyara.demo import DemoCloud, DemoPlanner
from lyara.models import Plan, RunRequest
from lyara.service import Assistant


class WorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'package.json').write_text('{"scripts":{"start":"node app.js"}}')
        (self.root / 'app.js').write_text('console.log("hello")')
        self.cloud = DemoCloud()
        self.service = Assistant(self.root, self.root / '.lyara', self.cloud.client(), DemoPlanner())

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def deployment(self):
        return self.service.start(RunRequest(message='Deploy this project', intent='deploy',
                                            project='new-app', plan_id='demo-plan', port=3000))

    def test_no_mutations_before_approval_and_full_deployment_after(self):
        result = self.deployment()
        self.assertEqual(result['status'], 'awaiting_approval')
        self.assertEqual(self.cloud.mutations, [])
        result = self.service.approve(result['id'], result['plan_digest'], True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([r[1] for r in self.cloud.mutations], [
            '/v1/projects', '/v2/projects/new-app/sources', '/v2/projects/new-app/releases'])
        self.assertTrue(result['verification']['health']['healthy'])
        self.assertTrue(result['results'][1]['release']['verified'])

    def test_rejected_plan_performs_no_mutation(self):
        result = self.deployment()
        result = self.service.approve(result['id'], result['plan_digest'], False)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.cloud.mutations, [])

    def test_wrong_digest_cannot_approve(self):
        result = self.deployment()
        with self.assertRaises(ValueError):
            self.service.approve(result['id'], 'wrong-digest', True)
        self.assertEqual(self.cloud.mutations, [])

    def test_resume_after_restart_does_not_repeat_actions(self):
        result = self.deployment()
        self.service.close()
        self.service = Assistant(self.root, self.root / '.lyara', self.cloud.client(), DemoPlanner())
        result = self.service.approve(result['id'], result['plan_digest'], True)
        count = len(self.cloud.mutations)
        again = self.service.approve(result['id'], result['plan_digest'], True)
        self.assertEqual(again['status'], 'completed')
        self.assertEqual(len(self.cloud.mutations), count)

    def test_changed_source_does_not_change_approved_archive(self):
        result = self.deployment()
        digest = result['artifact']['sha256']
        (self.root / 'app.js').write_text('changed after review')
        result = self.service.approve(result['id'], result['plan_digest'], True)
        self.assertEqual(result['artifact']['sha256'], digest)
        self.assertEqual(result['status'], 'completed')

    def test_failed_release_stops_dependent_domain_actions(self):
        self.cloud.fail_release = True
        result = self.service.start(RunRequest(message='Deploy', intent='deploy', project='new-app',
            plan_id='demo-plan', port=3000, domain='www.example.com'))
        result = self.service.approve(result['id'], result['plan_digest'], True)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(any(path == '/v1/domains' for _, path in self.cloud.mutations))

    def test_existing_app_cannot_be_recreated_on_auth_failure(self):
        self.cloud.denied = True
        result = self.deployment()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(self.cloud.mutations, [])

    def test_no_secret_in_persisted_run(self):
        result = self.service.start(RunRequest(message='diagnose', project='demo-app'))
        self.assertNotIn('demo-db-password', json.dumps(result))
        for path in (self.root / '.lyara').glob('*.sqlite*'):
            self.assertNotIn(b'demo-db-password', path.read_bytes())

    def test_mcp_proposal_does_not_implicitly_grant_approval(self):
        plan = Plan(summary='Restart', steps=[{'action': 'restart', 'title': 'restart',
                                                'reason': 'human requested', 'args': {}}])
        result = self.service.start(RunRequest(message='restart', project='demo-app', intent='operate'), plan=plan)
        self.assertEqual(result['status'], 'awaiting_approval')
        self.assertEqual(self.cloud.mutations, [])


if __name__ == '__main__':
    unittest.main()
