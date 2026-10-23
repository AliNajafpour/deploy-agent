import json
import sys
import tempfile
import unittest

import httpx

from lyara.models import RunRequest
from lyara.providers import APIPlanner, invoke_cli, parse_plan, provider_from_env


class ProviderChecks(unittest.TestCase):
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
