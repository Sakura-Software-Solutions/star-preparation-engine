import contextlib
import io
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from star_preparation.cli import main
from star_preparation.store import Store
from star_preparation.web import Application, make_server, serve, validate_lan_host


class LANTests(unittest.TestCase):
    def test_only_explicit_private_lan_or_vpn_interfaces_are_accepted(self):
        for host in ('192.168.1.50', '10.20.0.5', '172.16.2.10', '100.90.1.2', 'fd00::1234'):
            validate_lan_host(host)
        for host in ('0.0.0.0', '::', '127.0.0.1', '::1', '8.8.8.8', 'example.com', '169.254.1.1'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_lan_host(host)

    def test_lan_requires_tls_before_initializing_storage(self):
        with patch('star_preparation.web.Application') as application:
            with self.assertRaisesRegex(ValueError, '--tls-cert'):
                serve('192.168.1.50', lan=True)
            application.assert_not_called()

    def test_cli_lan_defaults_to_8443_and_errors_are_actionable(self):
        args = ['star-prep', 'serve', '--lan', '--host', '192.168.1.50', '--tls-cert', 'lan.crt', '--tls-key', 'lan.key']
        with patch('sys.argv', args), patch('star_preparation.web.serve') as run:
            self.assertEqual(main(), 0)
            self.assertEqual(run.call_args.kwargs['port'], 8443)
            self.assertTrue(run.call_args.kwargs['lan'])
        with patch('sys.argv', ['star-prep', 'serve', '--lan']), contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as exit_status:
                main()
            self.assertEqual(exit_status.exception.code, 2)
            self.assertIn('private LAN/VPN IP', errors.getvalue())
            self.assertNotIn('Traceback', errors.getvalue())

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for a temporary test certificate')
    def test_verified_https_sign_in_private_routes_and_secure_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / 'test.crt', root / 'test.key'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-keyout', str(key), '-out', str(cert), '-subj', '/CN=127.0.0.1',
                            '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, capture_output=True)
            Store(root / 'data').add_user('engineer', 'synthetic-lan-password', 'preparer')
            app = Application(root / 'data', shared=True, secure_cookies=True)
            server = make_server('127.0.0.1', 0, application=app, tls_cert=cert, tls_key=key)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f'https://127.0.0.1:{server.server_address[1]}'
            # Trust only the generated certificate; hostname verification stays on.
            trust = ssl.create_default_context(cafile=str(cert))
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(origin + '/api/runs', context=trust, timeout=5)
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                request = urllib.request.Request(origin + '/api/login',
                    json.dumps({'username': 'engineer', 'password': 'synthetic-lan-password'}).encode(),
                    headers={'Origin': origin, 'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(request, context=trust, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    cookie = response.headers['Set-Cookie']
                    self.assertIn('Secure', cookie)
                    self.assertIn('HttpOnly', cookie)
                request = urllib.request.Request(origin + '/api/runs', headers={'Cookie': cookie.split(';')[0]})
                with urllib.request.urlopen(request, context=trust, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)['runs'], [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
