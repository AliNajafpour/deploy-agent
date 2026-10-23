import json
import sys
import tempfile
import unittest

import httpx

from lyara.models import RunRequest
from lyara.providers import APIPlanner, invoke_cli, parse_plan, provider_from_env


class ProviderChecks(unittest.TestCase):
    def test_default_api_is_boxapi_luna_and_explicit_overrides_still_work(self):
        planner = provider_from_env({'LYARA_LLM_API_KEY': 'test-key-value'})
        self.assertIsInstance(planner, APIPlanner)
        self.assertEqual(planner.base_url, 'https://ai.boxapi.ir/ai/v1')
        self.assertEqual(planner.model, 'openai/gpt-6-luna')
        custom = provider_from_env({'LYARA_PROVIDER': 'api', 'LYARA_LLM_API_KEY': 'test-key-value',
            'LYARA_LLM_BASE_URL': 'https://example.com/v1', 'LYARA_MODEL': 'chosen-model'})
        self.assertEqual((custom.base_url, custom.model), ('https://example.com/v1', 'chosen-model'))
        self.assertEqual(provider_from_env({'LYARA_PROVIDER': 'codex'}).model, '')

    def test_cli_missing_login_gives_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, '-c', 'import json,sys; print(json.dumps({"is_error":True,"result":"Not logged in - Please run /login"})); sys.exit(1)']
            with self.assertRaisesRegex(RuntimeError, 'وارد حساب'):
                invoke_cli(command, 'synthetic', tmp)

    def test_unknown_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_plan('{"summary":"bad","steps":[{"action":"shell","title":"x","reason":"x","args":{"command":"rm"}}]}')

    def test_model_json_never_grants_approval(self):
        with self.assertRaises(ValueError):
            parse_plan('{"summary":"x","approved":true,"steps":[]}')

    def test_api_key_is_in_header_only_and_custom_provider_works(self):
        def handle(request):
            self.assertEqual(str(request.url), 'https://example.com/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-key-value')
            self.assertNotIn(b'test-key-value', request.content)
            body = json.loads(request.content)
            self.assertEqual(body['model'], 'chosen-model')
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': '{"summary":"No mutation needed","steps":[]}'}}]})
        planner = APIPlanner('test-key-value', 'https://example.com/v1', 'chosen-model', transport=httpx.MockTransport(handle))
        result = planner.plan(RunRequest(message='inspect', project='demo-app').model_dump(), {'exists': True})
        self.assertEqual(result.steps, [])

    def test_invalid_or_incomplete_api_output_is_not_accepted(self):
        planner = APIPlanner('isolated-key-value', 'https://example.com/v1', 'model', transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})))
        with self.assertRaises(RuntimeError):
            planner.plan({'message': 'test'}, {})

    def test_key_alone_never_selects_a_provider_silently(self):
        with self.assertRaises(ValueError):
            provider_from_env({'LYARA_PROVIDER': 'invalid', 'LYARA_LLM_API_KEY': 'test'})


if __name__ == '__main__':
    unittest.main()
