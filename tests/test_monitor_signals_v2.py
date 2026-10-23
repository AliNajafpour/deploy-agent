"""Deterministic persisted monitoring checks; no model or public-cloud calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyara.demo import DemoCloud
from lyara.monitoring import Monitoring
from lyara.service import Assistant
from tests.test_monitoring import DiagnosticPlanner


class MonitoringSignalChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cloud = DemoCloud()
        self.planner = DiagnosticPlanner()
        self.assistant = Assistant(self.root, self.root / '.lyara', self.cloud.client(), self.planner)
        self.monitoring = Monitoring(self.assistant)
        self.monitoring.configure('demo-app', True)
        self.moment, self.latency, self.pressure, self.metric_stamp = 0, 100, None, None
        self.unit, self.missing = 'percent', False
        original = self.assistant.observe

        def observe(project):
            result = original(project)
            for name in ('cpu', 'memory'):
                if self.missing:
                    result[name] = {'available': False}
                    continue
                stamp = self.moment if self.metric_stamp is None else self.metric_stamp
                result[name] = {'available': True, 'data': {'unit': self.unit, 'result': [{
                    'values': [[stamp - offset * 30, self.pressure if self.pressure is not None else 20]
                               for offset in range(4, -1, -1)]}]}}
            return result

        self.assistant.observe = observe
        self.assistant.cloud.health = lambda url: {'available': True, 'healthy': True, 'latency_ms': self.latency}

    def tearDown(self):
        self.monitoring.close()
        self.assistant.close()
        self.tmp.cleanup()

    def tick(self, moment, latency=None):
        self.moment = moment
        if latency is not None:
            self.latency = latency
        return self.monitoring.tick(now=moment)[0]

    def baseline(self):
        for moment in range(0, 300, 60):
            self.tick(moment)

    def test_slowdown_needs_baseline_three_checks_and_survives_restart(self):
        self.baseline()
        watch = self.tick(300, 1500)
        self.assertEqual(watch['latency_baseline_ms'], 100)
        self.assertEqual(self.monitoring.incidents(), [])
        self.monitoring.close()
        self.monitoring = Monitoring(self.assistant)
        self.tick(360)
        self.assertEqual(self.monitoring.incidents(), [])
        self.tick(420)
        incident = self.monitoring.incidents()[0]
        self.assertEqual(incident['reasons'], ['latency'])
        self.assertEqual(incident['active_causes'], ['latency'])
        for moment in range(480, 1080, 60):
            watch = self.tick(moment, 2000)
        self.assertEqual(watch['latency_baseline_ms'], 100)
        self.assertEqual(len(watch['latency_history']), 5)
        self.assertEqual(len(self.planner.calls), 1)
        self.assertEqual(self.cloud.mutations, [])
        self.assertEqual(self.assistant.get(incident['run_id'])['status'], 'awaiting_approval')

    def test_two_slow_checks_and_long_gap_do_not_confirm(self):
        self.baseline()
        self.tick(300, 1200)
        self.tick(360)
        watch = self.tick(900)
        self.assertEqual(watch['signal_counts']['latency'], 1)
        self.assertEqual(self.monitoring.incidents(), [])
        self.tick(960)
        self.assertEqual(self.monitoring.incidents(), [])

    def test_fewer_than_five_baseline_samples_or_small_relative_slowdown_is_insufficient(self):
        for moment in (0, 60, 120, 180):
            self.tick(moment)
        self.assertIsNone(self.monitoring.list()[0]['latency_baseline_ms'])
        self.assertEqual(self.monitoring.incidents(), [])
        self.tick(240)
        for moment in (300, 360, 420, 480):
            watch = self.tick(moment, 999)
        self.assertFalse(watch['latest']['signals']['latency'])
        self.assertEqual(self.monitoring.incidents(), [])

    def test_resource_pressure_deduplicates_and_http_success_does_not_resolve(self):
        self.pressure = 95
        for moment in (0, 60, 120):
            self.tick(moment)
        incident = self.monitoring.incidents()[0]
        self.assertEqual(set(incident['active_causes']), {'cpu', 'memory'})
        self.monitoring.close()
        self.monitoring = Monitoring(self.assistant)
        for moment in (180, 240):
            self.tick(moment)
        self.assertEqual(len(self.planner.calls), 1)
        self.missing = True
        self.tick(300)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'open')
        self.missing, self.pressure = False, 30
        self.tick(360)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'open')
        self.tick(420)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'resolved')
        self.assertEqual(self.cloud.mutations, [])

    def test_missing_units_and_stale_or_repeated_metric_windows_never_confirm(self):
        self.pressure, self.unit = 95, None
        for moment in (0, 60, 120):
            watch = self.tick(moment)
        self.assertIsNone(watch['latest']['signals']['cpu'])
        self.assertEqual(watch['status'], 'unknown')
        self.assertEqual(self.monitoring.incidents(), [])
        self.unit, self.metric_stamp = 'percent', 180
        for moment in (180, 240, 300, 360):
            watch = self.tick(moment)
        self.assertEqual(watch['signal_counts']['cpu'], 1)
        self.assertEqual(self.monitoring.incidents(), [])
        watch = self.tick(600)
        self.assertIsNone(watch['latest']['signals']['cpu'])
        self.assertEqual(watch['status'], 'unknown')
        self.assertEqual(watch['latest']['analysis']['resources']['cpu']['freshness'], 'stale')

    def test_missing_or_stale_data_interrupts_recovery(self):
        self.pressure = 95
        for moment in (0, 60, 120):
            self.tick(moment)
        self.pressure = 20
        self.tick(180)
        self.missing = True
        self.tick(240)
        self.missing = False
        self.tick(300)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'open')
        self.tick(360)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'resolved')

    def test_each_active_cause_must_recover_and_new_causes_do_not_create_storm(self):
        self.baseline()
        for moment in (300, 360, 420):
            self.tick(moment, 1500)
        self.pressure = 95
        for moment in (480, 540, 600):
            self.tick(moment)
        self.assertEqual(len(self.monitoring.incidents()), 1)
        self.assertEqual(set(self.monitoring.incidents()[0]['reasons']), {'latency', 'cpu', 'memory'})
        self.assertEqual(len(self.planner.calls), 1)
        self.tick(660, 100)
        self.tick(720)
        self.assertEqual(set(self.monitoring.incidents()[0]['active_causes']), {'cpu', 'memory'})
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'open')
        self.pressure = 20
        self.tick(780)
        self.tick(840)
        self.assertEqual(self.monitoring.incidents()[0]['state'], 'resolved')

    def test_history_is_bounded_and_destination_changes_reset_baseline(self):
        for moment in range(0, 7800, 60):
            watch = self.tick(moment)
        self.assertEqual(len(watch['latency_history']), 120)
        # Turning monitoring off and back on preserves the actual site's measured history.
        self.monitoring.configure('demo-app', False)
        watch = self.monitoring.configure('demo-app', True)
        self.assertEqual(watch['latency_baseline_ms'], 100)
        self.assertEqual(len(watch['latency_history']), 120)
        with patch('lyara.monitoring.public_https', return_value={'ip': '93.184.216.34'}):
            watch = self.monitoring.configure('demo-app', True, health_url='https://example.com/status')
        self.assertIsNone(watch['latency_baseline_ms'])
        self.assertEqual(watch['latency_history'], [])
        self.assertEqual(self.cloud.mutations, [])


if __name__ == '__main__':
    unittest.main()
