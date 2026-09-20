import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

from star_preparation.store import Store


@unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'ASGI test dependencies not installed')
class ASGITests(unittest.TestCase):
    def test_asgi_login_and_disabled_ephemeral_deployment(self):
        from fastapi.testclient import TestClient
        from star_preparation.cloud_app import app, application
        with tempfile.TemporaryDirectory() as directory:
            Store(directory).add_user('engineer', 'asgi-test-password', 'preparer')
            with patch.dict(os.environ, {'STAR_DATA_DIR': directory, 'STAR_SECURE_COOKIES': '1'}):
                application.cache_clear()
                client = TestClient(app, base_url='https://internal.example')
                self.assertEqual(client.get('/api/runs').status_code, 401)
                response = client.post('/api/login', json={'username':'engineer', 'password':'asgi-test-password'}, headers={'Origin':'https://internal.example'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get('/api/runs').status_code, 200)
                self.assertEqual(client.get('/app.js').status_code, 200)
                self.assertEqual(client.get('/').status_code, 200)
            with patch.dict(os.environ, {'VERCEL':'1', 'STAR_DATA_DIR':directory}):
                application.cache_clear()
                response = TestClient(app).get('/api/health')
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.json()['ready'])
            application.cache_clear()


if __name__ == '__main__':
    unittest.main()
