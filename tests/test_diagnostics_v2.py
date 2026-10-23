import copy
import json
import unittest
from datetime import datetime, timezone

from lyara.diagnostics import analyse


NOW = 1800000000


def metric(values, unit=None, **extra):
    data = {'result': [{'values': [[NOW - 120 + i * 30, str(value)] for i, value in enumerate(values)]}]}
    if unit is not None:
        data['unit'] = unit
    data.update(extra)
    return {'available': True, 'data': data}


class DiagnosticsChecks(unittest.TestCase):
    def test_regression_math_explicit_units_capacity_and_not_leak(self):
        observations = {'memory': metric([60, 70, 80, 90, 100], 'MiB', capacity=110, capacity_unit='MiB'),
                        'cpu': metric([91, 92, 93, 94, 95], 'percent')}
        result = analyse(observations, now=NOW)
        memory = result['resources']['memory']
        self.assertEqual(memory['sample_count'], 5)
        self.assertEqual(memory['freshness'], 'fresh')
        self.assertEqual(memory['trend'], 'increasing')
        self.assertAlmostEqual(memory['series'][0]['slope_per_second'], 1 / 3)
        self.assertIsNone(memory['pressure'])  # Final 3 straddle the pressure threshold.
        self.assertTrue(result['resources']['cpu']['pressure'])
        growth = next(item for item in result['findings'] if item['kind'] == 'memory_growth')
        self.assertEqual(growth['confidence'], 'medium')
        self.assertTrue(growth['missing'])
        self.assertFalse(result['limits']['memory_growth_is_leak_proof'])

    def test_unknown_units_missing_capacity_stale_and_missing_never_zero(self):
        for name in ('cpu', 'memory'):
            item = analyse({name: metric([.4, .5, .6, .8, .95])}, now=NOW)['resources'][name]
            self.assertIsNone(item['unit'])
            self.assertIsNone(item['pressure'])
            self.assertEqual(item['trend'], 'unknown')
        observations = {'memory': metric([91, 92, 93, 94, 95], 'MiB')}
        result = analyse(observations, now=NOW + 600)
        self.assertEqual(result['resources']['memory']['freshness'], 'stale')
        self.assertIsNone(result['resources']['memory']['pressure'])
        missing = analyse({}, now=NOW)['resources']['cpu']
        self.assertIsNone(missing['pressure'])
        self.assertEqual(missing['sample_count'], 0)
        self.assertEqual(missing['freshness'], 'unknown')

    def test_numeric_and_capacity_validation_and_irregular_samples(self):
        data = metric([90, 92, 94, 96, 98], 'MiB', capacity=100 * 1048576, capacity_unit='bytes')
        data['data']['result'][0]['values'].extend([[NOW, 'nan'], [NOW + 1000, '999'], [NOW - 30, True]])
        memory = analyse({'memory': data}, now=NOW)['resources']['memory']
        self.assertTrue(memory['pressure'])
        self.assertEqual(memory['sample_count'], 5)
        data['data']['capacity_unit'] = 'cores'
        self.assertIsNone(analyse({'memory': data}, now=NOW)['resources']['memory']['pressure'])
        # A burst of 5 values in 4 seconds is too short for a trend/pressure conclusion.
        data['data']['result'][0]['values'] = [[NOW - index, 99] for index in range(5)]
        self.assertEqual(analyse({'memory': data}, now=NOW)['resources']['memory']['trend'], 'unknown')

    def test_repeated_errors_settings_dependencies_oom_and_restart(self):
        logs = [{'message': 'ERROR connection timeout id=' + str(index), 'timestamp': NOW - index} for index in range(4)]
        logs += [{'message': 'KeyError: DATABASE_URL'}, {'message': "ModuleNotFoundError: No module named 'pandas'"},
                 {'message': 'Process exited code 137'}, {'message': 'password=do-not-share-value'}]
        result = analyse({'logs': {'available': True, 'data': logs}, 'events': {'available': True, 'data': {
            'events': [{'reason': 'OOMKilled', 'message': 'Container restarting'}]}}}, now=NOW)
        self.assertEqual(result['errors']['patterns'][0]['count'], 4)
        for kind in ('repeated_error', 'missing_configuration', 'dependency', 'oom', 'restart'):
            self.assertIn(kind, [item['kind'] for item in result['findings']])
        self.assertEqual(next(item for item in result['findings'] if item['kind'] == 'oom')['confidence'], 'high')
        self.assertNotIn('do-not-share-value', json.dumps(result))
        self.assertFalse(any(isinstance(item['confidence'], (int, float)) for item in result['findings']))

    def test_exit_137_does_not_prove_oom_and_memory_growth_is_separate(self):
        observations = {'logs': {'available': True, 'data': [{'message': 'exited code 137'}]},
                        'memory': metric([10, 20, 30, 40, 50], 'MiB')}
        result = analyse(observations, now=NOW)
        self.assertNotIn('oom', [item['kind'] for item in result['findings']])
        self.assertEqual(next(item for item in result['findings'] if item['kind'] == 'possible_oom')['confidence'], 'low')

    def test_build_output_dependency_lines_with_real_timestamps_and_secrets(self):
        timestamp = datetime.fromtimestamp(NOW - 30, timezone.utc).isoformat()
        observation = {'available': True, 'id': 'before.build_logs', 'data': {'buildOutput': [
            {'line': "ERROR Cannot find module 'express'; password=build-password-secret", 'stream': 'stderr', 'createdAt': timestamp},
            {'line': "ERROR Cannot find module 'express'", 'stream': 'stderr', 'createdAt': timestamp}]}}
        result = analyse({'build_logs': observation}, now=NOW)
        self.assertEqual(result['signals']['dependency'], 2)
        self.assertEqual(result['errors']['total'], 2)
        dependency = next(item for item in result['findings'] if item['kind'] == 'dependency')
        self.assertEqual(dependency['supporting'], ['before.build_logs.0', 'before.build_logs.1'])
        self.assertEqual(result['evidence']['before.build_logs.0']['timestamp'], NOW - 30)
        self.assertNotIn('build-password-secret', json.dumps(result))
        observation['available'] = False
        self.assertEqual(analyse({'build_logs': observation}, now=NOW)['signals']['dependency'], 0)

    def test_oom_denial_wrong_dimension_and_distinct_http_codes(self):
        rows = [{'message': 'OOMKilled=false'}, {'message': 'No OOM detected'},
                {'message': 'HTTP 404 ERROR'}, {'message': 'HTTP 500 ERROR'},
                {'message': 'DATABASE_URL is required'}]
        result = analyse({'logs': {'available': True, 'data': rows},
                          'memory': metric([.91, .92, .93, .94, .95], 'cores', capacity=1, capacity_unit='cores')}, now=NOW)
        self.assertEqual(result['signals']['oom'], 0)
        self.assertEqual(result['signals']['oom_denied'], 2)
        self.assertEqual(result['signals']['missing_configuration'], 1)
        self.assertIsNone(result['resources']['memory']['pressure'])
        signatures = {item['signature'] for item in result['errors']['patterns']}
        self.assertIn('HTTP 404 ERROR', signatures)
        self.assertIn('HTTP 500 ERROR', signatures)
        rows.append({'message': 'OOMKilled by kernel'})
        result = analyse({'logs': {'available': True, 'data': rows}}, now=NOW)
        oom = next(item for item in result['findings'] if item['kind'] == 'oom')
        self.assertEqual(len(oom['opposing']), 2)

    def test_release_correlation_uses_actual_timestamps_without_causal_claim(self):
        release_at = NOW - 200
        iso = datetime.fromtimestamp(release_at, timezone.utc).isoformat()
        observations = {'releases': {'available': True, 'data': {'releases': [{'createdAt': iso}]}},
                        'logs': {'available': True, 'data': [
                            {'message': 'ERROR timeout', 'timestamp': release_at - 1},
                            {'message': 'ERROR timeout', 'timestamp': release_at + 1},
                            {'message': 'ERROR timeout', 'timestamp': release_at + 60},
                            {'message': 'ERROR timeout', 'timestamp': release_at + 90},
                            {'message': 'ERROR timeout'}]}}
        result = analyse(observations, now=NOW)
        relation = result['release_correlation']
        self.assertEqual(relation['release_at'], release_at)
        self.assertEqual(relation['before_errors'], 1)
        self.assertEqual(relation['after_errors'], 3)
        self.assertFalse(relation['after_window_complete'])
        self.assertFalse(relation['causation_proven'])
        finding = next(item for item in result['findings'] if item['kind'] == 'release_correlation')
        self.assertTrue(finding['supporting'])
        self.assertTrue(finding['opposing'])
        del observations['releases']['data']['releases'][0]['createdAt']
        self.assertFalse(analyse(observations, now=NOW)['release_correlation']['available'])

    def test_series_not_mixed_or_invalidly_aggregated(self):
        data = metric([92, 93, 94, 95, 96], 'percent')
        other = copy.deepcopy(data['data']['result'][0])
        other['unit'] = 'unverified-unit'
        data['data']['result'].append(other)
        resource = analyse({'cpu': data}, now=NOW)['resources']['cpu']
        self.assertTrue(resource['pressure'])
        self.assertIsNone(resource['unit'])
        self.assertIsNone(resource['series'][1]['pressure'])
        self.assertEqual(resource['trend'], 'unknown')

    def test_bounds_redaction_no_mutation_and_malformed_input(self):
        secret = 'super-sensitive-credential'
        logs = [{'message': 'ERROR ' + secret + ' ' + 'x' * 20000}] * 1500
        observations = {'logs': {'available': True, 'data': logs}, 'cpu': {'available': True, 'data': {'result': [None]}}}
        snapshot = copy.deepcopy(observations)
        result = analyse(observations, now=NOW, secrets=[secret])
        self.assertEqual(result['errors']['total'], 1000)
        self.assertNotIn(secret, json.dumps(result))
        self.assertLess(len(json.dumps(result)), 50000)
        self.assertEqual(observations, snapshot)
        self.assertEqual(analyse(None, now=NOW)['errors']['total'], 0)
        self.assertEqual(analyse({'logs': {'available': False, 'data': logs}}, now=NOW)['errors']['total'], 0)


if __name__ == '__main__':
    unittest.main()
