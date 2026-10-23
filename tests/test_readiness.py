import json
import os
import socket
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from lyara.readiness import check_readiness


class ReadinessChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.envfile = self.root / '.env'
        self.envfile.write_text('', encoding='utf-8')
        self.env = {'LYARA_WORKSPACE': '.', 'LYARA_DATA_DIR': 'private',
                    'LYARA_LLM_API_KEY': 'model-key-do-not-print',
                    'LIARA_API_TOKEN': 'cloud-key-do-not-print',
                    'LYARA_ACCESS_TOKEN': 'RandomOperatorToken_1234567890abcdef',
                    'LYARA_PUBLIC_HOST': 'lyara.example.com'}

    def check(self, **kwargs):
        with patch('lyara.readiness.shutil.which', return_value='/usr/bin/node'):
            return check_readiness(self.envfile, self.env, **kwargs)

    @staticmethod
    def statuses(report):
        return {check['id']: check['status'] for check in report['checks']}

    def test_valid_config_is_offline_and_does_not_create_state(self):
        before = dict(os.environ)
        with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('network')):
            report = self.check(production=True)
        self.assertTrue(report['ready'])
        self.assertEqual(self.statuses(report)['hosting_options'], 'warning')
        self.assertFalse((self.root / 'private').exists())
        self.assertEqual(dict(os.environ), before)

    def test_missing_keys_and_malformed_host_fail_without_secret_echo(self):
        self.env['LYARA_LLM_API_KEY'] = ''
        self.env['LIARA_API_TOKEN'] = 'cloud-secret\ninvalid'
        self.env['LYARA_PUBLIC_HOST'] = 'https://secret-user:secret-password@example.com/path'
        report = self.check(production=True)
        self.assertFalse(report['ready'])
        for name in ['model_key', 'liara_token', 'public_host']:
            self.assertEqual(self.statuses(report)[name], 'error')
        output = json.dumps(report)
        for secret in ['cloud-secret', 'secret-user', 'secret-password', self.env['LYARA_ACCESS_TOKEN']]:
            self.assertNotIn(secret, output)

    def test_dotenv_does_not_interpolate_or_override_environment(self):
        self.envfile.write_text('LYARA_LLM_API_KEY=${EXTERNAL_SECRET}\nLIARA_API_TOKEN=file-key\n', encoding='utf-8')
        del self.env['LYARA_LLM_API_KEY']
        with patch.dict(os.environ, {'EXTERNAL_SECRET': 'must-not-read'}):
            report = self.check()
        self.assertEqual(self.statuses(report)['model_key'], 'error')
        self.assertEqual(self.statuses(report)['liara_token'], 'pass')
        self.assertNotIn('must-not-read', json.dumps(report))

    def test_production_requires_strong_access_token_and_hostname(self):
        for host in ['', 'localhost', 'example.com:443', '*.example.com', 'example.com/path', '127.0.0.1']:
            with self.subTest(host=host):
                self.env['LYARA_PUBLIC_HOST'] = host
                self.env['LYARA_ACCESS_TOKEN'] = 'short'
                statuses = self.statuses(self.check(production=True))
                self.assertEqual(statuses['public_host'], 'error')
                self.assertEqual(statuses['access_token'], 'error')

    def test_api_url_is_checked_without_requests(self):
        for url in ['http://example.com/v1', 'https://user:pass@example.com/v1', 'https://example.com/?key=secret', 'https://[invalid']:
            with self.subTest(url=url):
                self.env['LYARA_LLM_BASE_URL'] = url
                self.assertEqual(self.statuses(self.check())['model_url'], 'error')

    def test_directory_boundary_and_permissions(self):
        self.env['LYARA_DATA_DIR'] = '.'
        self.assertEqual(self.statuses(self.check())['paths'], 'error')
        self.env['LYARA_DATA_DIR'] = 'private'
        with patch('lyara.readiness.os.access', return_value=False):
            self.assertEqual(self.statuses(self.check())['data_directory'], 'error')

    def test_missing_corrupt_and_wrong_vault_key(self):
        data = self.root / 'private'
        data.mkdir()
        key = Fernet.generate_key()
        with closing(sqlite3.connect(data / 'inputs.sqlite')) as db, db:
            db.execute('CREATE TABLE inputs (encrypted BLOB)')
            db.execute('CREATE TABLE retired (encrypted BLOB)')
            db.execute('INSERT INTO inputs VALUES (?)', (Fernet(key).encrypt(b'private-value'),))
        self.assertEqual(self.statuses(self.check())['vault'], 'error')
        (data / 'inputs.key').write_bytes(b'corrupt-key')
        self.assertEqual(self.statuses(self.check())['vault'], 'error')
        (data / 'inputs.key').write_bytes(Fernet.generate_key())
        self.assertEqual(self.statuses(self.check())['vault'], 'error')
        (data / 'inputs.key').write_bytes(key)
        before = {p.name: p.read_bytes() for p in data.iterdir()}
        report = self.check()
        self.assertEqual(self.statuses(report)['vault'], 'pass')
        self.assertEqual(before, {p.name: p.read_bytes() for p in data.iterdir()})
        self.assertNotIn('private-value', json.dumps(report))

    def test_hosting_choices_validate_existing_contract(self):
        for options in ['{}', '[{"id":"a"}]', '[{"id":"a","title":"site","cost":"price"},{"id":"a","title":"site","cost":"price"}]']:
            with self.subTest(options=options):
                self.env['LYARA_HOSTING_OPTIONS'] = options
                self.assertEqual(self.statuses(self.check())['hosting_options'], 'error')
        self.env['LYARA_HOSTING_OPTIONS'] = '[{"id":"real-plan","title":"Site","cost":"Operator-reviewed terms"}]'
        self.assertEqual(self.statuses(self.check())['hosting_options'], 'pass')

    def test_cli_provider_requires_command_and_never_api_key(self):
        self.env['LYARA_PROVIDER'] = 'codex'
        self.env['LYARA_LLM_API_KEY'] = ''
        with patch('lyara.readiness.shutil.which', return_value=None):
            report = check_readiness(self.envfile, self.env)
        self.assertEqual(self.statuses(report)['provider_command'], 'error')
        self.assertNotIn('model_key', self.statuses(report))


if __name__ == '__main__':
    unittest.main()
