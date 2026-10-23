import asyncio
import contextlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lyara import cli
from lyara.mcp_server import create_mcp


class CliMcpTests(unittest.TestCase):
    def setUp(self):
        self.service = SimpleNamespace(
            controls=Mock(return_value={'stopped': True, 'generation': 7, 'managed_projects': []}),
            emergency=Mock(return_value={'stopped': False, 'generation': 8}),
            manage=Mock(return_value={'managed_projects': ['demo-app'], 'generation': 8}),
            hosting_catalog=Mock(return_value={'available': False, 'error': 'Unavailable; no guessed price'}),
            handoff=Mock(return_value={'issue': 'Known failure', 'source': {'sha256': 'a' * 64}}),
            answer=Mock(return_value={'status': 'awaiting_approval'}),
            start=Mock(return_value={'status': 'awaiting_approval'}),
            data_dir=Path('state'), vault=object(), close=Mock())

    def invoke(self, arguments, tty=False, answer=''):
        output, errors = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['lyara', *arguments]), patch('sys.stdin.isatty', return_value=tty), \
                patch('builtins.input', return_value=answer), patch.object(cli, 'create_assistant', return_value=self.service) as factory, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                cli.main()
                code = 0
            except SystemExit as error:
                code = error.code
        return code, output.getvalue(), errors.getvalue(), factory

    def test_permission_grants_and_emergency_release_require_exact_human_confirmation(self):
        for arguments in [['manage', 'demo-app', '--enable'], ['emergency', '--disable']]:
            self.assertEqual(self.invoke(arguments)[0], 1)
            self.assertEqual(self.invoke(arguments, tty=True, answer='yes')[0], 1)
        self.service.manage.assert_not_called()
        self.service.emergency.assert_not_called()
        code, _, _, factory = self.invoke(['manage', 'demo-app', '--enable'], tty=True, answer='MANAGE demo-app 7')
        self.assertEqual(code, 0)
        self.service.manage.assert_called_once_with('demo-app', True, 7)
        factory.assert_called_once_with(False, model=False)
        self.assertEqual(self.invoke(['emergency', '--disable'], tty=True, answer='RESUME 7')[0], 0)
        self.service.emergency.assert_called_once_with(False, 7)

    def test_restrictive_controls_are_safe_without_interactive_confirmation(self):
        self.assertEqual(self.invoke(['emergency', '--enable'])[0], 0)
        self.service.emergency.assert_called_once_with(True, 7)
        self.assertEqual(self.invoke(['manage', 'demo-app', '--disable'])[0], 0)
        self.service.manage.assert_called_once_with('demo-app', False, 7)

    def test_cli_does_not_reauthorize_after_concurrent_control_change(self):
        self.service.manage.side_effect = PermissionError('Control generation changed')
        code, _, errors, _ = self.invoke(['manage', 'demo-app', '--enable'], tty=True, answer='MANAGE demo-app 7')
        self.assertEqual(code, 1)
        self.assertIn('generation changed', errors)
        self.service.manage.assert_called_once_with('demo-app', True, 7)
        self.service.close.assert_called_once_with()

    def test_read_only_commands_do_not_load_a_model(self):
        for command, method in [('controls', 'controls'), ('hosting', 'hosting_catalog')]:
            code, output, _, factory = self.invoke([command])
            self.assertEqual(code, 0)
            self.assertIsInstance(json.loads(output), dict)
            factory.assert_called_once_with(False, model=False)
            getattr(self.service, method).assert_called()
        code, output, _, factory = self.invoke(['handoff', 'run-1', '--format', 'markdown'])
        self.assertEqual(code, 0)
        self.assertIn('Known failure', output)
        self.service.handoff.assert_called_once_with('run-1')
        factory.assert_called_once_with(False, model=False)

    def test_github_command_preserves_fixed_commit_provenance_and_never_starts_deployment(self):
        imported = {'source': 'upload:fixed', 'github': {'repository': 'https://github.com/owner/repo', 'commit': 'b' * 40}}
        with patch('lyara.github_source.import_github', return_value=imported) as importer:
            code, output, _, factory = self.invoke(['github', 'https://github.com/owner/repo', '--ref', 'main'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), imported)
        importer.assert_called_once_with('https://github.com/owner/repo', self.service.data_dir, self.service.vault, ref='main')
        factory.assert_called_once_with(False, model=False)
        self.service.start.assert_not_called()
        self.service.manage.assert_not_called()

    def test_hosting_fields_reach_core_without_synthesizing_ids(self):
        self.assertEqual(self.invoke(['deploy', 'new-app', '--source', 'upload:fixed', '--plan-id', 'plan-real',
            '--bundle-plan-id', 'bundle-real', '--network', 'network-real', '--network-name', 'private-network'])[0], 0)
        request = self.service.start.call_args.args[0]
        self.assertEqual((request.source, request.plan_id, request.bundle_plan_id, request.network, request.network_name),
                         ('upload:fixed', 'plan-real', 'bundle-real', 'network-real', 'private-network'))
        self.assertEqual(self.invoke(['answer', 'run-1', '--message', 'choose', '--hosting-choice', 'choice-real',
            '--bundle-plan-id', 'bundle-real', '--network', 'network-real'])[0], 0)
        answer = self.service.answer.call_args.args[1]
        self.assertEqual(answer['hosting_choice'], 'choice-real')
        self.assertEqual(answer['bundle_plan_id'], 'bundle-real')
        self.assertEqual(answer['network'], 'network-real')

    def test_mcp_only_exposes_readonly_control_catalog_and_handoff(self):
        async def check():
            server = create_mcp(self.service)
            tools = {tool.name: tool for tool in await server.list_tools()}
            for name in ['control_status', 'hosting_catalog', 'coding_handoff']:
                self.assertIn(name, tools)
                self.assertTrue(tools[name].annotations.readOnlyHint)
                self.assertFalse(tools[name].annotations.destructiveHint)
            for name in ['approve', 'execute', 'emergency', 'manage', 'set_emergency', 'set_managed']:
                self.assertNotIn(name, tools)
            await server.call_tool('control_status', {})
            await server.call_tool('hosting_catalog', {})
            await server.call_tool('coding_handoff', {'run_id': 'run-1'})
        asyncio.run(check())
        self.service.controls.assert_called_once_with()
        self.service.hosting_catalog.assert_called_once_with()
        self.service.handoff.assert_called_once_with('run-1')
        self.service.emergency.assert_not_called()
        self.service.manage.assert_not_called()


if __name__ == '__main__':
    unittest.main()
