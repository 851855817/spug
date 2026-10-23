"""Actual signed HTTP transport to a loopback fixture; never a real panel or CA."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
from unittest import TestCase
from urllib.parse import parse_qs, urlsplit

from .client import PanelClient
from .operations import get_operation
from .test_operations import PanelFixture, pem


class SignedPanel:
    """Store only request metadata; never put credentials or raw bodies in failures."""
    def __init__(self):
        self.fixture = PanelFixture()
        self.fixture.software['redis'] = {**copy.deepcopy(self.fixture.software['nginx']), 'name': 'redis'}
        self.requests, self.failures = [], []
        self.key = 'local-integration-fixture-key'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def signed_form(self):
                assert self.headers.get_content_type() == 'application/x-www-form-urlencoded'
                values = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode(),
                                  keep_blank_values=True)
                assert all(len(value) == 1 for value in values.values())
                values = {name: value[0] for name, value in values.items()}
                timestamp, token = values.pop('request_time'), values.pop('request_token')
                assert abs(int(timestamp) - time.time()) < 60
                expected = hashlib.md5((timestamp + hashlib.md5(owner.key.encode()).hexdigest()).encode()).hexdigest()
                assert token == expected
                return values

            def respond(self, body, content_type, filename=None):
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                if filename:
                    self.send_header('Content-Disposition', 'attachment; filename=' + filename.rsplit('/', 1)[1])
                self.end_headers()
                self.wfile.write(body)

            def reject(self, exc):
                owner.failures.append(type(exc).__name__)
                self.send_error(500)

            def do_GET(self):
                try:
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query)
                    assert parsed.path == '/download' and set(query) == {'filename'}
                    assert not self.signed_form()  # Signature stays in the GET form body.
                    filename = query['filename'][0]
                    assert filename.startswith(('/www/server/panel/rewrite/nginx/',
                                                '/www/server/panel/vhost/letsencrypt/'))
                    owner.requests.append(('/download', 'readonly-public-file', False))
                    self.respond(owner.fixture.file_bodies[filename].encode(), 'application/octet-stream', filename)
                except Exception as exc:
                    self.reject(exc)

            def do_POST(self):
                try:
                    params = self.signed_form()
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query)
                    assert set(query) == {'action'}
                    action = query['action'][0]
                    for name in ('id', 'p', 'limit', 'row', 'type_id'):
                        if name in params:
                            params[name] = int(params[name])
                    mutation = action in ('AddSite', 'multiple_basedir', 'SetRewriteLists', 'install_plugin',
                        'uninstall_plugin', 'InstallSoft', 'apply_cert_api', 'renew_cert', 'SetCertToSite') \
                        or action == 'upgrade_panel' and 'force' in params
                    owner.requests.append((parsed.path, action, mutation))
                    fixture = owner.fixture
                    result = fixture.request(parsed.path, action, params, mutation, allow_status_false=True)
                    if action == 'install_plugin':
                        fixture.software[params['sName']].update(setup=True, version=params['version'] + '.' + params['min_version'])
                        fixture.tasks[-1]['status'] = '1'
                    if action == 'InstallSoft':
                        fixture.libs[0]['status'], fixture.modules = True, ['redis']
                        fixture.tasks[-1]['status'] = '1'
                    if action in ('apply_cert_api', 'renew_cert'):
                        owner.certificate_order(pem())
                    if action == 'SetCertToSite':
                        fixture.ssl = {'status': True,
                            'csr': fixture.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'],
                            'key': 'NEVER_RETURN_SECRET', 'index': 'a' * 32, 'auth_type': 'http', 'type': 1}
                    if action == 'upgrade_panel' and 'force' in params:
                        fixture.info['version'] = params['version']
                    self.respond(json.dumps(result).encode(), 'application/json')
                except Exception as exc:
                    self.reject(exc)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.panel = SimpleNamespace(api_base_url='http://127.0.0.1:' + str(self.server.server_port),
                                     tls_ca='', tls_fingerprint='', get_api_key=lambda: self.key)

    def certificate_order(self, body):
        self.fixture.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid',
            'save_path': 'vhost/letsencrypt/example.com', 'ca': 'letsencrypt', 'private_key': 'NEVER_RETURN_SECRET'}]
        self.fixture.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = body

    def mutation_count(self):
        return sum(request[2] for request in self.requests)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class SignedHTTPIntegrationTests(TestCase):
    def exercise(self, panel, client, code, params, expected='success'):
        op = get_operation(code)
        preview = op.preflight(client, op.validate(params))
        self.assertEqual(preview['summary']['ready'], 1, code)
        item = preview['items'][0]
        mutations = panel.mutation_count()
        result = op.execute(client, item)
        self.assertLessEqual(panel.mutation_count() - mutations, 1, code)
        if result.get('remote', {}).get('next_action') == 'deploy':
            mutations = panel.mutation_count()
            result = op.dispatch(client, item, 'deploy', result['remote'])
            self.assertLessEqual(panel.mutation_count() - mutations, 1, code)
        if result['state'] in ('remote', 'uncertain'):
            mutations = panel.mutation_count()
            result = op.reconcile(client, item, result['remote'])
            self.assertEqual(panel.mutation_count(), mutations, code)
        self.assertEqual(result['state'], expected, code)
        if code == 'site_rewrite':
            self.assertTrue(result['evidence']['configuration_saved'])
            self.assertFalse(result['evidence']['runtime_verified'])
        # Do not use assertNotIn: its failure would print the complete raw payload.
        payload = json.dumps({'preview': preview, 'result': result})
        for secret in (panel.key, 'NEVER_RETURN_SECRET', 'PRIVATE_SECRET', 'BEGIN CERTIFICATE'):
            self.assertFalse(secret in payload, 'Public result contains sensitive fixture data: ' + code)

    def test_nine_operations_ten_flows_over_signed_http(self):
        with SignedPanel() as panel, PanelClient(panel.panel) as client:
            run = lambda code, params, **kwargs: self.exercise(panel, client, code, params, **kwargs)
            fixture = panel.fixture
            run('site_add', {'entries': [{'domains': ['http-test.example.com'],
                'path': '/www/wwwroot/http-test.example.com', 'php_version': '00'}]})
            run('site_basedir', {'sites': [1], 'enabled': True})
            run('site_rewrite', {'sites': [1], 'mode': 'custom', 'webserver': 'nginx',
                'rules': 'location / { try_files $uri $uri/ /index.php; }'}, expected='uncertain')
            fixture.file_bodies['/www/server/panel/rewrite/nginx/wordpress.conf'] = \
                'location / { try_files $uri $uri/ /index.php?$args; }'
            run('site_rewrite', {'sites': [1], 'mode': 'template', 'webserver': 'nginx',
                'template': 'wordpress'}, expected='uncertain')
            # This fixture models explicit deployment only, not panel auto-sync or real CA issuance.
            run('ssl_apply', {'sites': [1]})
            old = pem(days=10)
            panel.certificate_order(old)
            fixture.ssl['csr'] = old
            run('ssl_renew', {'sites': [1]})
            run('software_install', {'name': 'redis', 'm_version': '1.24', 'min_version': '0'})
            run('php_extension_install', {'php_version': '74', 'extensions': ['redis']})
            run('software_uninstall', {'name': 'redis'})
            fixture.info['version'] = '11.7.0'
            run('panel_upgrade', {'version': '13.1.0'})
            self.assertEqual(panel.failures, [])
            self.assertEqual(panel.mutation_count(), 12)
            self.assertTrue(any(path == '/download' for path, _, _ in panel.requests))
            self.assertFalse(any(action in ('GetFileBody', 'GetRewriteLists') for _, action, _ in panel.requests))
            self.assertEqual(fixture.file_writes, [])
            self.assertEqual(fixture.directory_writes, [])
