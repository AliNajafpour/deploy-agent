import asyncio
import gzip
import json
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from lyara.providers import APIPlanner, CLIPlanner, provider_from_env


def response(**fields):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': '{"summary":"پیشنهاد آماده است","steps":[]}'}}], **fields})


class ModelUsageChecks(unittest.TestCase):
    def planner(self, handler, **kwargs):
        return APIPlanner('private-model-key', 'https://models.example/v1', 'chosen-model',
            transport=httpx.MockTransport(handler), **kwargs)

    def test_actual_usage_and_reported_model_are_retained_once(self):
        planner = self.planner(lambda _: response(model='served-model', usage={
            'prompt_tokens': 10, 'completion_tokens': 7, 'total_tokens': 17, 'cost_usd': 0.004}))
        planner.plan({'message': 'بررسی کن'}, {})
        records = planner.drain_usage()
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]['model'], records[0]['requested_model']), ('served-model', 'chosen-model'))
        self.assertEqual((records[0]['input_tokens'], records[0]['output_tokens'], records[0]['total_tokens']), (10, 7, 17))
        self.assertEqual((records[0]['cost_usd'], records[0]['cost_kind']), (0.004, 'reported'))
        self.assertEqual(planner.drain_usage(), [])

    def test_unknown_cost_and_absent_usage_are_not_zero(self):
        planner = self.planner(lambda _: response())
        planner.plan({}, {})
        record = planner.drain_usage()[0]
        for key in ('input_tokens', 'output_tokens', 'total_tokens', 'cost_usd', 'model'):
            self.assertIsNone(record[key])
        self.assertEqual(record['cost_kind'], 'unknown')

    def test_estimates_require_explicit_rates_and_actual_counts(self):
        planner = self.planner(lambda _: response(usage={'input_tokens': 100, 'output_tokens': 50}),
            input_rate=2, output_rate=8)
        planner.plan({}, {})
        record = planner.drain_usage()[0]
        self.assertAlmostEqual(record['cost_usd'], 0.0006)
        self.assertEqual(record['cost_kind'], 'estimated')
        planner = self.planner(lambda _: response(usage={'input_tokens': 100}), input_rate=2, output_rate=8)
        planner.plan({}, {})
        self.assertIsNone(planner.drain_usage()[0]['cost_usd'])

    def test_unqualified_reported_cost_never_assumes_dollar_currency(self):
        planner = self.planner(lambda _: response(usage={'cost': 12}))
        planner.plan({}, {})
        record = planner.drain_usage()[0]
        self.assertIsNone(record['cost_usd'])
        self.assertEqual(record['reported_cost'], {'amount': 12, 'currency': None})

    def test_rate_configuration_is_explicit_validated_and_never_echoed(self):
        env = {'LYARA_LLM_API_KEY': 'private-model-key', 'LYARA_LLM_INPUT_USD_PER_MILLION': '2',
            'LYARA_LLM_OUTPUT_USD_PER_MILLION': '8'}
        planner = provider_from_env(env)
        self.assertEqual((planner.input_rate, planner.output_rate), (2, 8))
        for value in ('private-model-key', 'nan', '-1', 'inf', True):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                self.planner(lambda _: response(), input_rate=value)
            self.assertNotIn(str(value), str(error.exception))

    def test_retry_after_is_capped_and_failed_attempt_usage_is_retained(self):
        urls = []
        def handle(request):
            urls.append(str(request.url))
            if len(urls) == 1:
                return httpx.Response(429, headers={'Retry-After': '999999'}, json={
                    'usage': {'prompt_tokens': 4, 'completion_tokens': 0, 'cost_usd': 0.001}})
            return response(usage={'prompt_tokens': 9, 'completion_tokens': 2})
        planner = self.planner(handle)
        with patch('lyara.providers.time.sleep') as sleep:
            planner.plan({}, {})
        sleep.assert_called_once_with(planner.retry_after_cap)
        self.assertEqual(urls, ['https://models.example/v1/chat/completions'] * 2)
        records = planner.drain_usage()
        self.assertEqual([item['http_status'] for item in records], [429, 200])
        self.assertEqual([item['status'] for item in records], ['http_error', 'success'])
        self.assertEqual(records[0]['input_tokens'], 4)
        self.assertEqual(records[0]['cost_usd'], 0.001)

    def test_transient_failures_have_bounded_attempts_without_provider_fallback(self):
        urls = []
        def handle(request):
            urls.append(str(request.url))
            if len(urls) == 1:
                raise httpx.ConnectError('private-model-key must never appear', request=request)
            return httpx.Response(503, json={'error': 'private-model-key'})
        planner = self.planner(handle)
        with patch('lyara.providers.time.sleep'), self.assertRaises(RuntimeError) as error:
            planner.plan({}, {})
        self.assertEqual(len(urls), 3)
        self.assertEqual(set(urls), {'https://models.example/v1/chat/completions'})
        records = planner.drain_usage()
        self.assertEqual([item['status'] for item in records], ['transport_error', 'http_error', 'http_error'])
        self.assertNotIn('private-model-key', json.dumps(records) + str(error.exception))

    def test_auth_failures_and_invalid_plans_are_not_retried(self):
        for result in (httpx.Response(401, json={'error': 'private-model-key'}),
                       httpx.Response(200, json={'usage': {'prompt_tokens': 2}, 'choices': []})):
            calls = []
            def handle(_):
                calls.append(True)
                return result
            planner = self.planner(handle)
            with self.subTest(status=result.status_code), self.assertRaises(RuntimeError):
                planner.plan({}, {})
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(planner.drain_usage()), 1)

    def test_total_deadline_blocks_retry_and_late_success(self):
        planner = self.planner(lambda _: httpx.Response(429, headers={'Retry-After': '10'}))
        planner.request_deadline = 0.1
        with patch('lyara.providers.time.sleep') as sleep, self.assertRaisesRegex(RuntimeError, 'مهلت'):
            planner.plan({}, {})
        sleep.assert_not_called()
        self.assertEqual(len(planner.drain_usage()), 1)
        def late(_):
            time.sleep(0.25)
            return response(usage={'input_tokens': 1, 'output_tokens': 1})
        planner = self.planner(late)
        planner.request_deadline = 0.2
        with self.assertRaisesRegex(RuntimeError, 'زمان پاسخ'):
            planner.plan({}, {})
        self.assertEqual(planner.drain_usage()[0]['status'], 'deadline_exceeded')

    def test_fifo_admission_thread_local_usage_and_finite_queue_wait(self):
        entered, release = threading.Event(), threading.Event()
        seen, records, errors = [], {}, []
        def handle(request):
            name = json.loads(json.loads(request.content)['messages'][1]['content'].split('\nData:\n')[1])['request']['message']
            seen.append(name)
            if name == 'first':
                entered.set()
                if not release.wait(2):
                    raise AssertionError('test failed to release first request')
            return response(model=name, usage={'input_tokens': len(name), 'output_tokens': 1})
        planner = self.planner(handle)
        planner.queue_wait = 0.08
        def call(name):
            try:
                planner.plan({'message': name}, {})
                records[name] = planner.drain_usage()
            except Exception as error:
                errors.append((name, str(error)))
        first = threading.Thread(target=call, args=('first',))
        first.start()
        self.assertTrue(entered.wait(1))
        expired = threading.Thread(target=call, args=('expired',))
        expired.start()
        expired.join(1)
        self.assertFalse(expired.is_alive())
        self.assertEqual([item[0] for item in errors], ['expired'])
        planner.queue_wait = 1
        second = threading.Thread(target=call, args=('second',))
        second.start()
        until = time.monotonic() + 1
        while len(planner._queue) != 1 and time.monotonic() < until:
            time.sleep(0.001)
        self.assertEqual(len(planner._queue), 1)
        third = threading.Thread(target=call, args=('third',))
        third.start()
        until = time.monotonic() + 1
        while len(planner._queue) != 2 and time.monotonic() < until:
            time.sleep(0.001)
        self.assertEqual(len(planner._queue), 2)
        release.set()
        for thread in (first, second, third):
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(seen, ['first', 'second', 'third'])
        self.assertEqual({name: items[0]['model'] for name, items in records.items()},
            {'first': 'first', 'second': 'second', 'third': 'third'})
        self.assertEqual(planner.drain_usage(), [])

    def test_cli_unknown_usage_is_explicit_and_failure_can_be_drained(self):
        planner = CLIPlanner('codex', 'operator-choice')
        with patch.object(planner, '_plan', side_effect=RuntimeError('CLI failed')):
            with self.assertRaises(RuntimeError):
                planner.plan({}, {})
        record = planner.drain_usage()[0]
        self.assertEqual((record['provider'], record['status']), ('codex', 'error'))
        self.assertIsNone(record['cost_usd'])
        self.assertIsNone(record['model'])
        self.assertIsNone(record['input_tokens'])

    def test_claude_reported_usage_is_kept_without_guessing_missing_total(self):
        planner = CLIPlanner('claude')
        envelope = {'result': '{"summary":"آماده","steps":[]}', 'model': 'reported-claude',
                    'usage': {'input_tokens': 21, 'output_tokens': 8}, 'total_cost_usd': 0.005}
        with patch('lyara.providers.cli_command', return_value=['claude']), \
             patch('lyara.providers.invoke_cli', return_value=json.dumps(envelope)):
            planner.plan({}, {})
        record = planner.drain_usage()[0]
        self.assertEqual((record['model'], record['input_tokens'], record['output_tokens']), ('reported-claude', 21, 8))
        self.assertEqual((record['cost_usd'], record['cost_kind']), (0.005, 'reported'))
        self.assertIsNone(record['total_tokens'])

    def test_slow_stream_is_cancelled_at_total_deadline(self):
        closed = threading.Event()
        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    yield b' '
            async def aclose(self):
                closed.set()
        planner = self.planner(lambda _: httpx.Response(200, stream=SlowStream()))
        planner.request_deadline = 0.15
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'زمان پاسخ'):
            planner.plan({}, {})
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(closed.wait(1), 'timed-out network stream must close')
        self.assertEqual(planner.drain_usage()[0]['status'], 'deadline_exceeded')

    def test_decoded_gzip_response_is_not_decoded_twice_and_size_is_bounded(self):
        body = response(usage={'input_tokens': 5, 'output_tokens': 1}).content
        planner = self.planner(lambda _: httpx.Response(200, content=gzip.compress(body), headers={
            'content-encoding': 'gzip', 'content-length': str(len(gzip.compress(body)))}))
        planner.plan({}, {})
        self.assertEqual(planner.drain_usage()[0]['input_tokens'], 5)
        planner = self.planner(lambda _: httpx.Response(200, content=b'x' * 1_000_001))
        with self.assertRaisesRegex(RuntimeError, 'قابل پردازش'):
            planner.plan({}, {})
        self.assertEqual(planner.drain_usage()[0]['status'], 'invalid_response')

    def test_malformed_usage_never_becomes_a_number_or_leaks_credentials(self):
        planner = self.planner(lambda _: response(model='private-model-key', usage={
            'input_tokens': True, 'output_tokens': -1, 'total_tokens': 10 ** 400,
            'cost_usd': -3, 'cost': {'amount': 1, 'currency': 'private-model-key'}}))
        planner.plan({}, {})
        record = planner.drain_usage()[0]
        self.assertIsNone(record['input_tokens'])
        self.assertIsNone(record['output_tokens'])
        self.assertIsNone(record['total_tokens'])
        self.assertIsNone(record['cost_usd'])
        self.assertNotIn('private-model-key', json.dumps(record))


if __name__ == '__main__':
    unittest.main()
