import tempfile
import threading
import unittest
from pathlib import Path

from lyara.store import Store, UncertainAction


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'operations.sqlite'
        self.store = Store(self.path)

    def permission(self, **overrides):
        return {'project': 'demo-app', 'generation': self.store.controls()['generation'],
                'creating': False, **overrides}

    def test_default_read_only_and_explicit_create_exception(self):
        self.assertEqual(self.store.controls(), {'stopped': False, 'generation': 0, 'managed_projects': []})
        with self.assertRaises(PermissionError):
            self.store.assert_mutation('demo-app', 0)
        self.store.assert_mutation('demo-app', 0, creating=True)
        self.store.set_managed('demo-app', True)
        self.store.assert_mutation(**self.permission())
        with self.assertRaises(PermissionError):
            self.store.assert_mutation('other-app', self.store.controls()['generation'])

    def test_stops_and_scope_changes_invalidate_old_generations_permanently(self):
        managed = self.store.set_managed('demo-app', True)
        generation = managed['generation']
        stopped = self.store.set_emergency(True)
        self.assertTrue(stopped['stopped'])
        with self.assertRaises(PermissionError):
            self.store.assert_mutation('demo-app', stopped['generation'], creating=True)
        resumed = self.store.set_emergency(False)
        self.assertGreater(resumed['generation'], stopped['generation'])
        with self.assertRaises(PermissionError):
            self.store.assert_mutation('demo-app', generation)
        self.store.assert_mutation('demo-app', resumed['generation'])
        self.store.set_managed('demo-app', False)
        with self.assertRaises(PermissionError):
            self.store.assert_mutation('demo-app', self.store.controls()['generation'])

    def test_register_created_keeps_exact_plan_generation_but_never_revives_stale_plan(self):
        generation = self.store.controls()['generation']
        self.store.register_created('new-app', generation)
        self.assertEqual(self.store.controls()['generation'], generation)
        self.store.assert_mutation('new-app', generation)
        self.store.set_emergency(True)
        self.store.set_emergency(False)
        with self.assertRaises(PermissionError):
            self.store.register_created('other-app', generation)
        self.assertNotIn('other-app', self.store.controls()['managed_projects'])

    def test_state_and_operator_audit_survive_reopen(self):
        self.store.set_managed('demo-app', True)
        expected = self.store.set_emergency(True)
        another = Store(self.path)
        self.assertEqual(another.controls(), expected)
        history = another.control_history()
        self.assertEqual([item['operation'] for item in history], ['set_emergency', 'set_managed'])
        self.assertEqual(history[1]['project'], 'demo-app')
        self.assertEqual(history[0]['generation'], expected['generation'])
        self.store.set_emergency(False)
        self.assertFalse(another.controls()['stopped'])

    def test_stale_control_changes_fail_atomically_without_audit_or_scope_changes(self):
        before = self.store.controls()
        another = Store(self.path)
        fresh = another.set_emergency(True, expected_generation=before['generation'])
        with self.assertRaises(PermissionError):
            self.store.set_emergency(False, expected_generation=before['generation'])
        with self.assertRaises(PermissionError):
            self.store.set_managed('demo-app', True, expected_generation=before['generation'])
        self.assertEqual(self.store.controls(), fresh)
        self.assertEqual(len(self.store.control_history()), 1)
        self.store.set_managed('demo-app', True, expected_generation=fresh['generation'])
        self.assertIn('demo-app', self.store.controls()['managed_projects'])

    def test_write_once_checks_scope_and_stop_before_function_or_reservation(self):
        calls = []
        with self.assertRaises(PermissionError):
            self.store.write_once('run:0', lambda: calls.append(1), permission=self.permission())
        self.assertEqual(calls, [])
        self.assertEqual(self.store.actions('run'), [])
        self.store.set_managed('demo-app', True)
        allowed = self.permission()
        self.store.set_emergency(True)
        with self.assertRaises(PermissionError):
            self.store.write_once('run:0', lambda: calls.append(1), permission=allowed)
        self.assertEqual(calls, [])
        self.assertEqual(self.store.actions('run'), [])

    def test_reserved_external_operation_is_not_claimed_cancelled(self):
        self.store.set_managed('demo-app', True)
        permission = self.permission()
        entered, finish = threading.Event(), threading.Event()
        outcome = []
        def external():
            entered.set()
            self.assertTrue(finish.wait(3))
            return {'releaseID': 'already-sent'}
        def dispatch():
            outcome.append(self.store.write_once('run:0', external, permission=permission))
        worker = threading.Thread(target=dispatch)
        worker.start()
        self.assertTrue(entered.wait(3))
        stopped = Store(self.path).set_emergency(True)
        self.assertTrue(stopped['stopped'])
        self.assertEqual(self.store.actions('run')[0]['status'], 'started')
        finish.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [{'releaseID': 'already-sent'}])
        self.assertEqual(self.store.actions('run')[0]['status'], 'succeeded')
        with self.assertRaises(PermissionError):
            self.store.write_once('run:1', lambda: None, permission=permission)

    def test_uncertain_result_is_never_repeated_even_after_new_approval(self):
        self.store.set_managed('demo-app', True)
        calls = []
        def unknown():
            calls.append(1)
            raise UncertainAction('response lost')
        with self.assertRaises(UncertainAction):
            self.store.write_once('run:0', unknown, permission=self.permission())
        self.store.set_emergency(True)
        self.store.set_emergency(False)
        with self.assertRaises(UncertainAction):
            self.store.write_once('run:0', unknown, permission=self.permission())
        self.assertEqual(calls, [1])

    def test_invalid_permissions_fail_closed(self):
        for project in ['', '../app', 'two words', 'a' * 64]:
            with self.assertRaises(ValueError):
                self.store.set_managed(project, True)
        for value in [1, 'false', None]:
            with self.assertRaises(ValueError):
                self.store.set_emergency(value)
        for generation in [True, '0', None, -1]:
            with self.assertRaises(ValueError):
                self.store.assert_mutation('demo-app', generation, creating=True)


if __name__ == '__main__':
    unittest.main()
