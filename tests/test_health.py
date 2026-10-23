import socket
import unittest
from unittest.mock import patch

import httpx

from lyara.liara import LiaraClient


class HealthChecks(unittest.TestCase):
    def test_health_pins_validated_ip_and_preserves_tls_hostname_without_auth(self):
        seen = []
        def handle(request):
            seen.append(request)
            return httpx.Response(200)
        health_client = httpx.Client(transport=httpx.MockTransport(handle))
        with LiaraClient('cloud-private-token') as cloud:
            with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]), patch('lyara.liara.httpx.Client', return_value=health_client):
                result = cloud.health('https://example.com/health')
        self.assertTrue(result['healthy'])
        self.assertTrue(result['probe_attempted'])
        self.assertEqual(seen[0].url.host, '93.184.216.34')
        self.assertEqual(seen[0].headers['Host'], 'example.com')
        self.assertEqual(seen[0].extensions['sni_hostname'], 'example.com')
        self.assertNotIn('Authorization', seen[0].headers)

    def test_private_destination_is_rejected_without_request(self):
        with LiaraClient('test') as cloud:
            with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]):
                result = cloud.health('https://example.com')
        self.assertFalse(result['available'])
        self.assertFalse(result['probe_attempted'])

    def test_transport_failure_counts_only_after_safe_destination_validation(self):
        def fail(request):
            raise httpx.ConnectTimeout('test timeout', request=request)
        health_client = httpx.Client(transport=httpx.MockTransport(fail))
        with LiaraClient('cloud-private-token') as cloud:
            with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]), patch('lyara.liara.httpx.Client', return_value=health_client):
                result = cloud.health('https://example.com')
            with patch('socket.getaddrinfo', side_effect=socket.gaierror()):
                unresolved = cloud.health('https://example.com')
        self.assertFalse(result['available'])
        self.assertFalse(result['healthy'])
        self.assertTrue(result['probe_attempted'])
        self.assertFalse(unresolved['probe_attempted'])


if __name__ == '__main__':
    unittest.main()
