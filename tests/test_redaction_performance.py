import subprocess
import sys
import unittest
from pathlib import Path

from lyara.security import redact


class RedactionPerformanceChecks(unittest.TestCase):
    def test_long_untrusted_text_finishes_without_truncation(self):
        script = """
from lyara.security import redact
for text in ('a1' * 12000, 'secret' * 4000, 'password' + ' ' * 24000,
             'a1' * 12000 + ' password=hidden-value'):
    expected = text.replace('hidden-value', '[REDACTED]')
    assert redact(text) == expected
"""
        try:
            result = subprocess.run(
                [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, timeout=3,
            )
        except subprocess.TimeoutExpired:
            self.fail('Redacting 24 KB of untrusted text exceeded three seconds')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_credential_forms_and_known_values_remain_redacted(self):
        cases = [
            ('password=hunter2', 'password=[REDACTED]'),
            ('{"secret": "hidden"}', '{"secret": "[REDACTED]"}'),
            ("process.env['API_KEY'] = 'hidden';", "process.env['API_KEY'] = '[REDACTED]';"),
            ('DB_PASSWORD=hidden', 'DB_PASSWORD=[REDACTED]'),
            ('--password=hidden', '--password=[REDACTED]'),
            ('api-key=hidden', 'api-key=[REDACTED]'),
            ('PRIVATE-KEY=hidden', 'PRIVATE-KEY=[REDACTED]'),
            ('Authorization: Bearer hidden', 'Authorization: Bearer [REDACTED]'),
            ('postgresql://owner:hidden@db.example/app', 'postgresql://owner:[REDACTED]@db.example/app'),
            ('...postgres://owner:hidden@db.example/app', '...postgres://owner:[REDACTED]@db.example/app'),
            ('sk-secretvalue sk-ant-secretvalue', '[REDACTED] [REDACTED]'),
            ('a' * 24000 + 'password=hidden', 'a' * 24000 + 'password=[REDACTED]'),
        ]
        for text, expected in cases:
            with self.subTest(text=text[:60]):
                self.assertEqual(redact(text), expected)
        self.assertEqual(redact('opaque-value-1234 tiny tinyword', ['opaque-value-1234', 'tiny']),
                         '[REDACTED] [REDACTED] tinyword')


if __name__ == '__main__':
    unittest.main()
