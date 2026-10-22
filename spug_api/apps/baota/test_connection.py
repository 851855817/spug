import datetime
import hashlib
import importlib
import ipaddress
import json
import socket
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.test import SimpleTestCase, TestCase

from apps.baota import tests as legacy
from apps.baota.models import Panel


class LocalPanel:
    """Real local transport; captures only fixture credentials and never contacts a customer."""

    def __init__(self, response=None, status=200, tls=False, headers=None, raw=None, disconnect=False,
                 response_gate=None):
        self.received = []
        self.response = response if response is not None else {'version': '13.1.0', 'system': 'Ubuntu 22.04 Linux'}
        self.raw, self.status, self.headers = raw, status, headers or {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.received.append((self.path, parse_qs(body.decode()), dict(self.headers)))
                if disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(owner.status)
                for key, value in owner.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if response_gate is not None:
                    response_gate.wait(timeout=1)
                try:
                    self.wfile.write(owner.raw if owner.raw is not None else json.dumps(owner.response).encode())
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.directory = tempfile.TemporaryDirectory()
        self.pem, self.fingerprint = '', ''
        if tls:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
            now = datetime.datetime.now(datetime.timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                           .public_key(key.public_key()).serial_number(x509.random_serial_number())
                           .not_valid_before(now - datetime.timedelta(minutes=1))
                           .not_valid_after(now + datetime.timedelta(days=2))
                           .add_extension(x509.SubjectAlternativeName([
                               x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                           .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                           .sign(key, hashes.SHA256()))
            self.pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
            self.fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
            cert_path, key_path = Path(self.directory.name) / 'cert.pem', Path(self.directory.name) / 'key.pem'
            cert_path.write_text(self.pem)
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                   serialization.PrivateFormat.TraditionalOpenSSL,
                                                   serialization.NoEncryption()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert_path), str(key_path))
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.url = '{}://127.0.0.1:{}'.format('https' if tls else 'http', self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()


class ConnectionAPITests(TestCase):
    setUp = legacy.BaotaAPITests.setUp
    make_user = legacy.BaotaAPITests.make_user
    authenticate = legacy.BaotaAPITests.authenticate
    request = legacy.BaotaAPITests.request
    group = legacy.BaotaAPITests.group
    panel = legacy.BaotaAPITests.panel

    def local_panel(self, **kwargs):
        server = LocalPanel(**kwargs)
        self.addCleanup(server.close)
        return server

    def configured(self, server=None, **kwargs):
        return self.panel(api_base_url=server.url if server else 'https://api.example.com:8888/proxy',
                          api_key='fixture-api-secret', **kwargs)

    def test_legacy_records_default_to_no_api_configuration(self):
        record = self.panel()
        self.assertIs(record.get('has_api_key'), False)
        self.assertEqual(record.get('api_base_url'), '')
        self.assertEqual(record.get('api_config_revision'), 0)

    def test_api_key_encrypted_never_returned_and_login_entry_is_independent(self):
        record = self.configured()
        self.assertTrue(record.get('has_api_key'))
        self.assertNotIn('api_key', record)
        self.assertNotIn('api_key_ciphertext', record)
        saved = Panel.objects.get(pk=record['id'])
        self.assertNotIn('fixture-api-secret', saved.api_key_ciphertext)
        self.assertEqual(saved.get_api_key(), 'fixture-api-secret')
        self.assertEqual(saved.url, 'https://example.com:8888/secure-entry?from=console')
        self.assertEqual(saved.api_base_url, 'https://api.example.com:8888/proxy')
        self.assertEqual(self.request('get', '/baota/'), [record])

    def test_blank_api_key_preserves_and_clear_is_explicit(self):
        record = self.configured()
        self.assertTrue(record.get('has_api_key'))
        before = Panel.objects.get(pk=record['id']).api_key_ciphertext
        record.update(api_key='', password='', name='renamed')
        saved = self.request('post', '/baota/', record)
        self.assertEqual(Panel.objects.get(pk=record['id']).api_key_ciphertext, before)
        self.assertEqual(saved['api_config_revision'], record['api_config_revision'])
        saved.update(clear_api_key=True)
        cleared = self.request('post', '/baota/', saved)
        self.assertFalse(cleared['has_api_key'])
        self.assertEqual(Panel.objects.get(pk=record['id']).api_key_ciphertext, '')

    def test_invalid_api_addresses_and_conflicting_tls_are_rejected(self):
        group = self.group()
        for address in ['ftp://example.com', '//example.com', 'https://u:p@example.com',
                        'https://example.com?x=1', 'https://example.com?', 'https://example.com#',
                        'https://example.com:bad', 'https://example.com\\@other',
                        'https://example.com\n', 'https://exa mple.com']:
            with self.subTest(address=address):
                self.panel_request_invalid(group, api_base_url=address)
        self.panel_request_invalid(group, api_base_url='https://example.com', tls_fingerprint='invalid')
        self.panel_request_invalid(group, api_base_url='https://example.com', tls_ca='not a CA')
        self.panel_request_invalid(group, api_base_url='https://example.com',
                                   tls_ca='not a CA', tls_fingerprint='a' * 64)

    def panel_request_invalid(self, group, **kwargs):
        return self.request('post', '/baota/', dict(name='bad', group_id=group['id'],
                            url='https://example.com/login', username='a', password='secret', **kwargs), success=False)

    def test_connection_uses_saved_credentials_system_query_and_records_version(self):
        server = self.local_panel()
        record = self.configured(server)
        result = self.request('post', '/baota/connection/', {'id': record['id'], 'api_key': 'ignored'})
        self.assertEqual(result['api_version'], '13.1.0')
        self.assertTrue(result['api_checked_at'])
        path, body, _ = server.received[0]
        self.assertEqual(path, '/system?action=GetSystemTotal')
        timestamp = body['request_time'][0]
        expected = hashlib.md5((timestamp + hashlib.md5(b'fixture-api-secret').hexdigest()).encode()).hexdigest()
        self.assertEqual(body['request_token'], [expected])
        self.assertNotIn('api_key', body)

    def test_configuration_changes_clear_check_but_login_edit_does_not(self):
        server = self.local_panel()
        record = self.configured(server)
        checked = self.request('post', '/baota/connection/', {'id': record['id']})
        checked.update(name='renamed', password='', api_key='')
        saved = self.request('post', '/baota/', checked)
        self.assertEqual(saved['api_checked_at'], checked['api_checked_at'])
        self.assertEqual(saved['api_config_revision'], checked['api_config_revision'])
        saved['api_key'] = 'new-key'
        changed = self.request('post', '/baota/', saved)
        self.assertEqual(changed['api_checked_at'], '')
        self.assertEqual(changed['api_version'], '')
        self.assertEqual(changed['api_config_revision'], saved['api_config_revision'] + 1)

    def test_edit_permission_required_for_connection_and_certificate_checks(self):
        record = self.configured()
        self.authenticate(self.make_user('readonly', perms={'panel': ['view']}))
        self.request('post', '/baota/connection/', {'id': record['id']}, success=False)
        self.request('post', '/baota/certificate/', {'id': record['id']}, success=False)

    def test_unsaved_or_unconfigured_connections_are_rejected(self):
        self.request('post', '/baota/connection/', {}, success=False)
        self.request('post', '/baota/connection/', {'id': 999}, success=False)
        record = self.panel()
        self.request('post', '/baota/connection/', {'id': record['id']}, success=False)

    def test_certificate_inspection_never_sends_api_credentials_or_changes_trust(self):
        server = self.local_panel(tls=True)
        record = self.configured(server)
        result = self.request('post', '/baota/certificate/', {'id': record['id']})
        self.assertEqual(result['fingerprint'], server.fingerprint)
        self.assertIn('localhost', result['subject'])
        self.assertTrue(result['expires_at'])
        self.assertEqual(server.received, [])
        self.assertEqual(Panel.objects.get(pk=record['id']).tls_fingerprint, '')

    def test_connection_race_does_not_mark_new_configuration_checked(self):
        record = self.configured()
        self.assertTrue(record.get('has_api_key'))
        from apps.baota.client import PanelClient

        def concurrent_edit(client, *args, **kwargs):
            Panel.objects.filter(pk=record['id']).update(api_config_revision=record['api_config_revision'] + 1,
                                                       api_base_url='https://changed.example.com')
            return {'version': '13.1.0', 'system': 'Linux'}

        with patch.object(PanelClient, 'request', concurrent_edit):
            self.request('post', '/baota/connection/', {'id': record['id']}, success=False)
        panel = Panel.objects.get(pk=record['id'])
        self.assertEqual(panel.api_checked_at, '')
        self.assertEqual(panel.api_version, '')

    def test_certificate_race_does_not_offer_fingerprint_for_changed_address(self):
        record = self.configured()

        def concurrent_edit(address):
            Panel.objects.filter(pk=record['id']).update(api_config_revision=record['api_config_revision'] + 1)
            return {'fingerprint': 'a' * 64, 'subject': 'CN=old', 'expires_at': 'future'}

        with patch('apps.baota.connection_views.inspect_certificate', concurrent_edit):
            self.request('post', '/baota/certificate/', {'id': record['id']}, success=False)

    def test_ca_is_preserved_on_legacy_edit_and_only_public_status_is_returned(self):
        server = self.local_panel(tls=True)
        record = self.configured(server, tls_ca=server.pem)
        self.assertTrue(record['has_tls_ca'])
        self.assertNotIn('tls_ca', record)
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(record))
        record.update(password='', name='legacy edit')
        saved = self.request('post', '/baota/', record)
        self.assertTrue(saved['has_tls_ca'])
        self.assertEqual(Panel.objects.get(pk=record['id']).tls_ca, server.pem)

    def test_windows_and_raw_authentication_errors_are_not_recorded(self):
        for response in [{'version': '13.1.0', 'system': 'Windows Server 2022'},
                         {'version': '13.1.0', 'platform': 'macOS'},
                         {'status': False, 'msg': 'fixture-api-secret raw-token raw-body'}]:
            with self.subTest(response=response):
                server = self.local_panel(response=response)
                record = self.configured(server, group_id=self.group(str(len(Panel.objects.all())))['id'])
                result = self.client.post('/baota/connection/', json.dumps({'id': record['id']}),
                                          content_type='application/json')
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json()['error'])
                self.assertNotIn('fixture-api-secret', result.content.decode())
                self.assertNotIn('raw-body', result.content.decode())
                self.assertEqual(Panel.objects.get(pk=record['id']).api_version, '')


class PanelTransportTests(SimpleTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('apps.baota.client'), 'PanelClient is not implemented')
        self.client_module = importlib.import_module('apps.baota.client')

    def local_panel(self, **kwargs):
        server = LocalPanel(**kwargs)
        self.addCleanup(server.close)
        return server

    def make_client(self, server, **kwargs):
        panel = Panel(api_base_url=server.url, **kwargs)
        panel.get_api_key = lambda: 'fixture-api-secret'
        client = self.client_module.PanelClient(panel)
        self.addCleanup(client.close)
        return client

    def test_signing_is_double_md5_and_reserved_fields_cannot_be_overridden(self):
        server = self.local_panel()
        client = self.make_client(server)
        with patch('apps.baota.client.time.time', return_value=1234567890):
            self.assertEqual(client.request('/system', action='GetSystemTotal', params={'x': '1'}), server.response)
        body = server.received[0][1]
        self.assertEqual(body['request_time'], ['1234567890'])
        self.assertEqual(body['request_token'], [hashlib.md5(
            ('1234567890' + hashlib.md5(b'fixture-api-secret').hexdigest()).encode()).hexdigest()])
        for field in ['request_time', 'request_token', 'action']:
            with self.subTest(field=field), self.assertRaises(self.client_module.PanelAPIError):
                client.request('/system', params={field: 'forged'})
        self.assertEqual(len(server.received), 1)

    def test_cross_origin_query_and_unsafe_paths_are_rejected_before_delivery(self):
        server = self.local_panel()
        client = self.make_client(server)
        for path in ['https://evil.example/x', '//evil.example/x', 'system', '/system?x=1',
                     '/system#x', '/\\evil', '/sys\ntem']:
            with self.subTest(path=path), self.assertRaises(self.client_module.PanelAPIError):
                client.request(path)
        self.assertEqual(server.received, [])

    def test_redirect_is_refused_without_following_or_retrying(self):
        target = self.local_panel()
        source = self.local_panel(status=307, headers={'Location': target.url + '/system'})
        with self.assertRaises(self.client_module.PanelAPIError):
            self.make_client(source).request('/system')
        self.assertEqual(len(source.received), 1)
        self.assertEqual(target.received, [])

    def test_redirect_body_is_never_prefetched_by_requests(self):
        gate = threading.Event()
        target = self.local_panel()
        source = self.local_panel(status=307, headers={'Location': target.url + '/system'}, response_gate=gate)
        try:
            with self.assertRaises(self.client_module.PanelAPIError) as raised:
                self.make_client(source).request('/system', timeout=(1, 0.05))
            self.assertIn('重定向', str(raised.exception))
        finally:
            gate.set()
        self.assertEqual(target.received, [])

    def test_response_is_bounded_without_trusting_content_length(self):
        source = self.local_panel(raw=b'x' * (1024 * 1024 + 1))
        with self.assertRaises(self.client_module.PanelAPIError):
            self.make_client(source).request('/system')
        self.assertEqual(len(source.received), 1)

    def test_fingerprint_mismatch_prevents_signed_http_request(self):
        server = self.local_panel(tls=True)
        mismatch = ('0' if server.fingerprint[0] != '0' else '1') + server.fingerprint[1:]
        with self.assertRaises(self.client_module.PanelAPIError):
            self.make_client(server, tls_fingerprint=mismatch).request('/system')
        self.assertEqual(server.received, [])
        result = self.make_client(server, tls_fingerprint=server.fingerprint).request('/system')
        self.assertEqual(result, server.response)
        self.assertEqual(len(server.received), 1)

    def test_system_tls_rejects_self_signed_but_imported_ca_works(self):
        server = self.local_panel(tls=True)
        with self.assertRaises(self.client_module.PanelAPIError):
            self.make_client(server).request('/system')
        self.assertEqual(server.received, [])
        self.assertEqual(self.make_client(server, tls_ca=server.pem).request('/system'), server.response)

    def test_errors_are_redacted_and_mutation_delivery_is_uncertain(self):
        server = self.local_panel()
        client = self.make_client(server)
        for error in [requests.ReadTimeout('fixture-api-secret request_token raw-body'),
                      requests.ConnectionError('fixture-api-secret request_token raw-body')]:
            for mutating in (False, True):
                with self.subTest(error=type(error).__name__, mutating=mutating):
                    with patch.object(client.session, 'post', side_effect=error):
                        with self.assertRaises(self.client_module.PanelAPIError) as raised:
                            client.request('/site', action='AddSite', mutating=mutating)
                    self.assertEqual(raised.exception.uncertain, mutating)
                    self.assertNotIn('fixture-api-secret', str(raised.exception))
                    self.assertNotIn('raw-body', str(raised.exception))

    def test_real_delivery_loss_is_uncertain_without_retry(self):
        server = self.local_panel(disconnect=True)
        with self.assertRaises(self.client_module.PanelAPIError) as raised:
            self.make_client(server).request('/site', action='AddSite', mutating=True)
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(len(server.received), 1)

    def test_real_read_timeout_is_uncertain_without_retry(self):
        gate = threading.Event()
        server = self.local_panel(response_gate=gate)
        try:
            with self.assertRaises(self.client_module.PanelAPIError) as raised:
                self.make_client(server).request('/site', action='AddSite', mutating=True, timeout=(1, 0.05))
            self.assertTrue(raised.exception.uncertain)
        finally:
            gate.set()
        self.assertEqual(len(server.received), 1)

    def test_invalid_response_never_exposes_raw_body(self):
        server = self.local_panel(raw=b'fixture-api-secret request_token raw-body')
        with self.assertRaises(self.client_module.PanelAPIError) as raised:
            self.make_client(server).request('/system')
        self.assertNotIn('fixture-api-secret', str(raised.exception))
        self.assertNotIn('raw-body', str(raised.exception))

    def test_proxy_prefix_is_kept_separate_from_login_entry(self):
        server = self.local_panel()
        client = self.make_client(server)
        client.base_url += '/proxy'
        client.request('/system', action='GetSystemTotal')
        self.assertEqual(server.received[0][0], '/proxy/system?action=GetSystemTotal')

    def test_close_removes_imported_ca_and_prevents_further_requests(self):
        server = self.local_panel(tls=True)
        client = self.make_client(server, tls_ca=server.pem)
        ca_path = Path(client.verify)
        self.assertTrue(ca_path.exists())
        with client:
            self.assertEqual(client.request('/system'), server.response)
        self.assertFalse(ca_path.exists())
        with self.assertRaises(self.client_module.PanelAPIError):
            client.request('/system')

    def test_timeout_override_must_be_finite_and_positive(self):
        server = self.local_panel()
        client = self.make_client(server)
        for timeout in [None, (2, 3)]:
            self.assertEqual(client.request('/system', timeout=timeout), server.response)
        for timeout in [0, -1, float('inf'), (1, None), (1, float('nan'))]:
            with self.subTest(timeout=timeout), self.assertRaises(self.client_module.PanelAPIError):
                client.request('/system', timeout=timeout)
