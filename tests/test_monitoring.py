import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from lyara.demo import DemoCloud
from lyara.liara import LiaraClient
from lyara.models import Plan
from lyara.monitoring import Monitoring
from lyara.service import Assistant


class DiagnosticPlanner:
    name = 'offline-demo'

    def __init__(self):
        self.calls = []

    def plan(self, request, context):
        self.calls.append((request, context))
        return Plan(summary='Review a possible restart.', steps=[{
            'action': 'restart', 'title': 'Restart', 'reason': 'Needs human review.', 'args': {}}])


class MonitoringChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cloud = DemoCloud()
        self.planner = DiagnosticPlanner()
        self.assistant = Assistant(self.root, self.root / '.lyara', self.cloud.client(), self.planner)
        self.monitoring = Monitoring(self.assistant)

    def tearDown(self):
        self.monitoring.close()
        self.assistant.close()
        self.tmp.cleanup()

    def test_opt_in_persistence_confirmation_dedup_and_recovery(self):
        with patch.object(self.assistant, 'observe', wraps=self.assistant.observe) as observe:
            self.monitoring.tick(now=0)
            observe.assert_not_called()
            self.monitoring.configure('demo-app', True)
            self.cloud.fail_release = True
            first = self.monitoring.tick(now=0)[0]
            self.assertEqual(first['status'], 'pending')
            self.assertEqual(self.monitoring.incidents(), [])
            self.assertEqual(self.monitoring.tick(now=59), [])
            self.monitoring.close()
            self.monitoring = Monitoring(self.assistant)
            self.assertTrue(self.monitoring.list()[0]['enabled'])
            confirmed = self.monitoring.tick(now=60)[0]
            self.assertEqual(confirmed['status'], 'unhealthy')
            incident = self.monitoring.incidents()[0]
            run = self.assistant.get(incident['run_id'])
            self.assertEqual(run['status'], 'awaiting_approval')
            self.assertEqual(run['request']['intent'], 'diagnose')
            self.monitoring.tick(now=120)
            self.assertEqual(len(self.monitoring.incidents()), 1)
            self.assertEqual(len(self.planner.calls), 1)
            self.cloud.fail_release = False
            self.assertEqual(self.monitoring.tick(now=180)[0]['status'], 'healthy')
            self.assertEqual(self.monitoring.incidents()[0]['state'], 'resolved')
            self.cloud.fail_release = True
            self.monitoring.tick(now=240)
            self.monitoring.tick(now=300)
            self.assertEqual(len(self.monitoring.incidents()), 2)
            self.assertEqual(len(self.planner.calls), 2)
        self.assertEqual(self.cloud.mutations, [])

    def test_unknown_health_interrupts_failure_streak_and_does_not_resolve(self):
        self.monitoring.configure('demo-app', True)
        self.cloud.fail_release = True
        self.monitoring.tick(now=0)
        original = self.assistant.cloud.health
        self.assistant.cloud.health = lambda url: {'available': False, 'healthy': False}
        self.assertEqual(self.monitoring.tick(now=60)[0]['status'], 'unknown')
        self.assistant.cloud.health = original
        self.monitoring.tick(now=120)
        self.assertEqual(self.monitoring.incidents(), [])
        self.monitoring.tick(now=180)
        self.assistant.cloud.health = lambda url: {'available': False, 'healthy': False}
        self.monitoring.tick(now=240)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'open')

    def test_transport_failures_confirm_one_incident_and_success_resolves_it(self):
        self.monitoring.configure('demo-app', True)
        def refused(request):
            raise httpx.ConnectError('Connection refused', request=request)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        def health(url):
            with patch('socket.getaddrinfo', return_value=addresses), patch('lyara.liara.httpx.Client',
                    return_value=httpx.Client(transport=httpx.MockTransport(refused))):
                return LiaraClient.health(self.assistant.cloud, url)
        with patch.object(self.assistant.cloud, 'health', side_effect=health):
            self.assertEqual(self.monitoring.tick(now=0)[0]['status'], 'pending')
            self.assertEqual(self.monitoring.tick(now=60)[0]['status'], 'unhealthy')
            self.monitoring.tick(now=120)
        self.assertEqual(len(self.monitoring.incidents()), 1)
        self.assertEqual(len(self.planner.calls), 1)
        self.assertEqual(self.monitoring.tick(now=180)[0]['status'], 'healthy')
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'resolved')
        self.assertEqual(self.cloud.mutations, [])

    def test_missing_api_telemetry_is_unknown_and_raw_units_are_not_inferred(self):
        self.monitoring.configure('demo-app', True)
        with patch.object(self.assistant, 'observe', return_value={'project': {'available': False}}):
            watch = self.monitoring.tick(now=0)[0]
        self.assertEqual(watch['status'], 'unknown')
        self.assertEqual(self.monitoring.incidents(), [])
        watch = self.monitoring.tick(now=60)[0]
        self.assertEqual(watch['latest']['observations']['cpu']['data']['result'][0]['values'][-1][1], '0.55')
        self.assertNotIn('percent', json.dumps(watch))
        original = self.assistant.observe
        def empty_metrics(project):
            result = original(project)
            result['cpu']['data'] = {'result': []}
            return result
        with patch.object(self.assistant, 'observe', side_effect=empty_metrics):
            self.assertEqual(self.monitoring.tick(now=120)[0]['status'], 'unknown')

    def test_secrets_never_reach_monitoring_database_or_diagnostic(self):
        secret = 'long-env-value-leaked-in-logs'
        self.cloud.apps['demo-app']['envs'].append({'key': 'SERVICE_SECRET', 'value': secret})
        original = self.assistant.cloud.observe
        def observe(project):
            value = original(project)
            value['logs']['data'].append({'message': 'Failed using ' + secret})
            return value
        self.assistant.cloud.observe = observe
        self.monitoring.configure('demo-app', True)
        self.cloud.fail_release = True
        self.monitoring.tick(now=0)
        self.monitoring.tick(now=60)
        with self.assistant.store.connect() as db:
            persisted = '\n'.join(str(row) for row in db.execute('SELECT payload FROM monitoring_watches'))
            persisted += '\n'.join(str(row) for row in db.execute('SELECT payload FROM monitoring_incidents'))
        self.assertNotIn(secret, persisted)
        self.assertNotIn(secret, json.dumps(self.planner.calls))
        self.assertNotIn('demo-db-password', persisted)
        self.assertEqual(self.cloud.mutations, [])

    def test_newly_known_secrets_are_redacted_again_on_read(self):
        self.monitoring.configure('demo-app', True)
        self.cloud.fail_release = True
        self.monitoring.tick(now=0)
        self.monitoring.tick(now=60)
        self.assistant.observed_values['demo-app'].append('Database connection timeout')
        self.assertNotIn('Database connection timeout', json.dumps(self.monitoring.list()))
        self.assistant.observed_values['demo-app'].append('https://demo-app.liara.run')
        self.assertNotIn('https://demo-app.liara.run', json.dumps(self.monitoring.incidents()))

    def test_opt_out_stops_future_reads_and_resets_pending_streak(self):
        self.monitoring.configure('demo-app', True)
        self.cloud.fail_release = True
        self.monitoring.tick(now=0)
        self.monitoring.configure('demo-app', False)
        with patch.object(self.assistant, 'observe') as observe:
            self.monitoring.tick(now=60)
            observe.assert_not_called()
        self.monitoring.configure('demo-app', True)
        self.assertEqual(self.monitoring.tick(now=120)[0]['status'], 'pending')
        self.assertEqual(self.monitoring.incidents(), [])

    def test_validation_limits_and_public_https_destination(self):
        for args in [('Bad Project', True), ('demo-app', 'yes'), ('demo-app', True, 59),
                     ('demo-app', True, 86401), ('demo-app', True, True)]:
            with self.assertRaises(ValueError):
                self.monitoring.configure(*args)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=addresses):
            for url in ['http://example.com', 'https://example.com', 'https://user:pass@example.com',
                        'https://example.com/?token=private']:
                with self.assertRaises(ValueError):
                    self.monitoring.configure('demo-app', True, health_url=url)
        with patch('socket.getaddrinfo', side_effect=socket.gaierror('unavailable')):
            with self.assertRaises(ValueError):
                self.monitoring.configure('demo-app', True, health_url='https://example.com')
        for index in range(25):
            self.monitoring.configure('app-' + str(index), True)
        with self.assertRaises(ValueError):
            self.monitoring.configure('app-26', True)
        self.monitoring.configure('app-0', False)
        self.monitoring.configure('app-26', True)

    def test_worker_does_not_overlap_active_assistant_and_close_does_not_wait_for_network(self):
        self.monitoring.configure('demo-app', True)
        held = threading.Event()
        release = threading.Event()
        def hold_lock():
            with self.assistant.lock:
                held.set()
                release.wait(3)
        thread = threading.Thread(target=hold_lock)
        thread.start()
        self.assertTrue(held.wait(1))
        try:
            with patch.object(self.assistant, 'observe') as observe:
                self.assertEqual(self.monitoring.tick(now=0), [])
                observe.assert_not_called()
        finally:
            release.set()
            thread.join(2)
        entered = threading.Event()
        finish = threading.Event()
        original = self.assistant.observe
        def slow_observe(project):
            entered.set()
            finish.wait(3)
            return original(project)
        with patch.object(self.assistant, 'observe', side_effect=slow_observe):
            self.monitoring.start()
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.monitoring.tick(now=0), [])
            started = time.monotonic()
            self.monitoring.close()
            self.assertLess(time.monotonic() - started, 0.8)
            finish.set()
            self.monitoring._thread.join(2)
        self.assertEqual(self.planner.calls, [])

    def test_idle_scheduler_makes_no_cloud_calls(self):
        with patch.object(self.assistant, 'observe') as observe, patch.object(self.assistant.cloud, 'health') as health:
            self.monitoring.start()
            time.sleep(0.05)
            self.monitoring.close()
            observe.assert_not_called()
            health.assert_not_called()

    def test_diagnostic_failure_is_recorded_once_and_never_retried(self):
        self.monitoring.configure('demo-app', True)
        self.cloud.fail_release = True
        self.monitoring.tick(now=0)
        with patch.object(self.assistant, 'process', side_effect=RuntimeError('model unavailable')) as process:
            self.monitoring.tick(now=60)
            self.monitoring.tick(now=120)
            process.assert_called_once()
        self.assertTrue(self.monitoring.incidents()[0]['run_id'])
        self.assertTrue(self.monitoring.incidents()[0]['error'])
        self.assertEqual(self.cloud.mutations, [])

    def test_diagnostic_reuses_uploaded_source_and_survives_restart(self):
        self.deployed('good-deploy', 'upload:known', 'active-release', 'active-source')
        self.cloud.releases_by_app['demo-app'] = [{'_id': 'active-release', 'state': 'READY'}]
        for index in range(60):
            self.assistant.new({'project': 'unrelated-app', 'intent': 'diagnose', 'message': str(index)})
        self.deployed('failed-deploy', 'upload:failed', 'failed-release', 'failed-source', action='repair_deploy', verified=False)
        self.assistant.new({'project': 'demo-app', 'intent': 'repair', 'source': 'upload:new-attempt', 'message': 'Prepare'})
        self.assertNotIn('good-deploy', [run['id'] for run in self.assistant.runs()])
        self.monitoring.configure('demo-app', True)
        self.cloud.fail_release = True
        self.monitoring.tick(now=0)
        self.monitoring.tick(now=60)
        run_id = self.monitoring.incidents()[0]['run_id']
        self.assertEqual(self.assistant.get(run_id)['request']['source'], 'upload:known')
        self.monitoring.close()
        self.monitoring = Monitoring(self.assistant)
        self.monitoring.tick(now=120)
        self.assertEqual(self.monitoring.incidents()[0]['run_id'], run_id)
        self.assertEqual(len(self.planner.calls), 1)
        self.assertEqual(self.cloud.mutations, [])

    def deployed(self, run_id, source, release_id, source_id, action='deploy', verified=True):
        self.assistant.store.save(run_id, {'id': run_id, 'request': {'project': 'demo-app', 'intent': 'deploy', 'source': source},
            'plan': {'steps': [{'action': action, 'args': {'platform': 'node', 'port': 3000}}]}, 'status': 'completed' if verified else 'failed',
            'results': [{'releaseID': release_id, 'sourceID': source_id, 'release': {'verified': verified}}]})

    def test_source_selection_matches_current_release_and_resolves_redeploy_source(self):
        self.deployed('original', 'upload:original', 'original-release', 'original-source')
        self.deployed('newer', 'upload:newer', 'newer-release', 'newer-source', action='repair_deploy')
        self.deployed('rollback', '.', 'rollback-release', 'original-source', action='redeploy')
        self.deployed('failed', 'upload:failed', 'failed-release', 'failed-source', verified=False)
        self.assertEqual(self.assistant.store.latest_source('demo-app', 'original-release'), 'upload:original')
        self.assertEqual(self.assistant.store.latest_source('demo-app', 'rollback-release'), 'upload:original')
        self.assertEqual(self.assistant.store.latest_source('demo-app'), 'upload:original')
        self.assertEqual(self.assistant.store.latest_source('demo-app', 'external-release'), '.')

    def test_unexecuted_deployment_never_supplies_diagnostic_source(self):
        self.assistant.new({'project': 'demo-app', 'intent': 'deploy', 'source': 'upload:not-deployed', 'message': 'Prepare'})
        self.assertEqual(self.assistant.store.latest_source('demo-app'), '.')


if __name__ == '__main__':
    unittest.main()
