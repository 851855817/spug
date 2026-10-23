"""Bounded signed upload/download transport against an actual loopback HTTP server."""
import hashlib
import io
import json
import socket
import ssl
import tempfile
import threading
import time
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import TestCase
from urllib.parse import parse_qs, quote, urlsplit

from .client import MAX_RESPONSE_BYTES, PanelAPIError, PanelClient


class UploadHTTPPanel:
    def __init__(self, *, body=b'{"status":true}', status=200, content_type='application/json',
                 disposition=None, headers=None, trickle=None, disconnect=False, no_length=False,
                 read_delay=0, declared_length=None, certificate_server=None):
        self.key = 'fixture-upload-key'
        self.received, self.failures = [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                try:
                    if read_delay:
                        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                        time.sleep(read_delay)
                    request_body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if self.command == 'POST':
                        assert parsed.path == '/files' and not query
                        message = BytesParser(policy=policy.default).parsebytes(
                            ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode()
                            + request_body)
                        assert message.is_multipart()
                        values, files = {}, []
                        for part in message.iter_parts():
                            name = part.get_param('name', header='content-disposition')
                            if name == 'zunfile':
                                files.append((part.get_filename(), part.get_payload(decode=True)))
                            else:
                                assert name not in values
                                values[name] = part.get_payload(decode=True).decode('utf-8')
                        assert values.pop('action') == 'UploadFile'
                        assert len(files) == 1
                    else:
                        assert parsed.path == '/download' and set(query) == {'filename'}
                        assert self.headers.get_content_type() == 'application/x-www-form-urlencoded'
                        values = {name: value[0] for name, value in parse_qs(request_body.decode()).items()}
                        files = []
                    timestamp, signature = values.pop('request_time'), values.pop('request_token')
                    assert abs(int(timestamp) - time.time()) < 60
                    expected = hashlib.md5((timestamp + hashlib.md5(owner.key.encode()).hexdigest()).encode()).hexdigest()
                    assert signature == expected
                    owner.received.append((self.command, query, values, files, dict(self.headers)))
                    if disconnect:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    if trickle == 'headers':
                        wire = (b'HTTP/1.0 200 OK\r\nX-Trickle: ' + b'x' * 100 + b'\r\n\r\n')
                    else:
                        self.send_response(status)
                        self.send_header('Content-Type', content_type)
                        if disposition:
                            self.send_header('Content-Disposition', disposition)
                        if not no_length:
                            self.send_header('Content-Length', str(len(body) if declared_length is None else declared_length))
                        for key, value in (headers or {}).items():
                            self.send_header(key, value)
                        self.end_headers()
                        wire = body
                    try:
                        if trickle:
                            for byte in wire:
                                self.wfile.write(bytes([byte]))
                                self.wfile.flush()
                                time.sleep(0.015)
                        else:
                            self.wfile.write(wire)
                    except OSError:
                        pass
                except Exception as exc:
                    owner.failures.append(type(exc).__name__)
                    try:
                        self.send_error(500)
                    except OSError:
                        pass

            do_POST = handle_request
            do_GET = handle_request

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        if certificate_server is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate_server.directory.name + '/cert.pem',
                                    certificate_server.directory.name + '/key.pem')
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.panel = SimpleNamespace(api_base_url=('https' if certificate_server else 'http')
                                     + '://127.0.0.1:' + str(self.server.server_port), tls_ca='',
                                     tls_fingerprint=certificate_server.fingerprint if certificate_server else '',
                                     get_api_key=lambda: self.key)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class RecordingBytesIO(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size < 0 or size > 65536:
            raise AssertionError('Transport attempted an unbounded file read')
        return super().read(size)


class UploadTransportTests(TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(PanelClient, 'upload_file', None)), 'Signed upload is not implemented')
        self.assertTrue(callable(getattr(PanelClient, 'file_sha256', None)), 'Bounded file hashing is not implemented')

    def panel(self, **kwargs):
        server = UploadHTTPPanel(**kwargs)
        self.addCleanup(server.close)
        client = PanelClient(server.panel)
        self.addCleanup(client.close)
        return server, client

    def test_upload_is_signed_multipart_and_streams_exact_remaining_binary_bytes(self):
        server, client = self.panel()
        content = bytes(range(256)) * 1024
        stream = RecordingBytesIO(b'prefix' + content)
        stream.seek(6)
        result = client.upload_file('/www/wwwroot/站点', '报表 "一".bin', stream)
        self.assertIs(result['status'], True)
        self.assertFalse(stream.closed)
        self.assertGreater(len(stream.read_sizes), 2)
        self.assertEqual(server.failures, [])
        method, query, values, files, headers = server.received[0]
        self.assertEqual((method, query, values), ('POST', {}, {'path': '/www/wwwroot/站点'}))
        self.assertEqual(files, [('报表 "一".bin', content)])
        self.assertNotIn('Transfer-Encoding', headers)
        self.assertTrue(headers['Content-Length'].isdigit())
        self.assertFalse(client.session.trust_env)

    def test_regular_staged_file_upload_is_supported(self):
        server, client = self.panel()
        with tempfile.TemporaryFile() as stream:
            stream.write(b'\x00\xffbinary\r\n')
            stream.seek(0)
            client.upload_file('/', 'a.bin', stream)
        self.assertEqual(server.received[0][3], [('a.bin', b'\x00\xffbinary\r\n')])

    def test_invalid_upload_inputs_and_oversized_streams_are_rejected_before_http(self):
        server, client = self.panel()
        for directory, filename in [('relative', 'a'), ('/x/../y', 'a'), ('//x', 'a'),
                                    ('/x&y', 'a'), ('/x|y', 'a'), ('/x', '../a'),
                                    ('/x', 'a/b'), ('/x', 'a\\b'), ('/x', '.'),
                                    ('/x', '..'), ('/x', 'a\r\nx'), ('/x', 'a&b'),
                                    ('/x/\ud800', 'a')]:
            with self.subTest(directory=directory, filename=filename), self.assertRaises(PanelAPIError) as raised:
                client.upload_file(directory, filename, io.BytesIO(b'a'))
            self.assertFalse(raised.exception.uncertain)
        for maximum in (0, -1, True, 20 * 1024 * 1024 + 1):
            with self.subTest(maximum=maximum), self.assertRaises(PanelAPIError):
                client.upload_file('/x', 'a', io.BytesIO(b'a'), maximum=maximum)
        stream = RecordingBytesIO(b'x' * 1025)
        with self.assertRaises(PanelAPIError):
            client.upload_file('/x', 'a', stream, maximum=1024)
        self.assertEqual(stream.read_sizes, [])
        self.assertEqual(stream.tell(), 0)
        for stream in (io.StringIO('text'), iter([b'a'])):
            with self.subTest(stream_type=type(stream).__name__), self.assertRaises(PanelAPIError):
                client.upload_file('/x', 'a', stream)
        self.assertEqual(server.received, [])

    def test_upload_requires_exact_true_status_and_errors_never_echo_remote_data(self):
        for body in (b'{"status":false,"msg":"fixture-upload-key raw-secret"}', b'{"status":1}',
                     b'{"status":"true"}', b'{}', b'[]', b'raw-secret fixture-upload-key'):
            with self.subTest(body_kind=body[:12]):
                server, client = self.panel(body=body)
                with self.assertRaises(PanelAPIError) as raised:
                    client.upload_file('/x', 'a', io.BytesIO(b'a'))
                self.assertTrue(raised.exception.uncertain)
                self.assertFalse('raw-secret' in str(raised.exception))
                self.assertFalse(server.key in str(raised.exception))
                self.assertEqual(len(server.received), 1)

    def test_upload_bounds_json_response_without_content_length(self):
        _, client = self.panel(body=b'x' * (MAX_RESPONSE_BYTES + 1), no_length=True)
        with self.assertRaises(PanelAPIError) as raised:
            client.upload_file('/x', 'a', io.BytesIO(b'a'))
        self.assertTrue(raised.exception.uncertain)

    def test_redirects_and_disconnects_never_retry_upload(self):
        target, _ = self.panel()
        for kwargs in ({'status': 307, 'headers': {'Location': target.panel.api_base_url + '/files'}},
                       {'disconnect': True}):
            source, client = self.panel(**kwargs)
            with self.assertRaises(PanelAPIError) as raised:
                client.upload_file('/x', 'a', io.BytesIO(b'a'))
            self.assertTrue(raised.exception.uncertain)
            self.assertEqual(len(source.received), 1)
        self.assertEqual(target.received, [])

    def test_upload_total_deadline_bounds_continuous_response_headers_and_body(self):
        for phase in ('headers', 'body'):
            with self.subTest(phase=phase):
                _, client = self.panel(trickle=phase)
                started = time.monotonic()
                with self.assertRaises(PanelAPIError) as raised:
                    client.upload_file('/x', 'a', io.BytesIO(b'a'), timeout=(0.05, 0.05))
                self.assertLess(time.monotonic() - started, 0.4)
                self.assertTrue(raised.exception.uncertain)
                self.assertIn('超时', str(raised.exception))

    def test_upload_total_deadline_bounds_a_peer_that_does_not_read_body(self):
        _, client = self.panel(read_delay=0.6)
        started = time.monotonic()
        with self.assertRaises(PanelAPIError) as raised:
            client.upload_file('/x', 'large.bin', io.BytesIO(b'a' * (20 * 1024 * 1024)),
                               timeout=(0.05, 0.05))
        self.assertLess(time.monotonic() - started, 0.4)
        self.assertTrue(raised.exception.uncertain)
        self.assertIn('超时', str(raised.exception))

    def test_upload_checks_total_deadline_between_continuous_send_chunks(self):
        class SlowSource(io.BytesIO):
            def read(self, size=-1):
                if size:
                    time.sleep(0.01)
                return super().read(size)

        _, client = self.panel()
        started = time.monotonic()
        with self.assertRaises(PanelAPIError) as raised:
            client.upload_file('/x', 'a', SlowSource(b'a' * (1024 * 1024)), timeout=(0.05, 0.05))
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(raised.exception.uncertain)
        self.assertIn('超时', str(raised.exception))

    def test_both_file_transports_verify_tls_fingerprint_before_sending_signatures(self):
        from .test_connection import LocalPanel

        certificate = LocalPanel(tls=True)
        self.addCleanup(certificate.close)
        for operation in ('upload', 'sha256'):
            with self.subTest(operation=operation):
                kwargs = {} if operation == 'upload' else {
                    'body': b'abc', 'content_type': 'application/octet-stream',
                    'disposition': 'attachment; filename=a.bin'}
                server, client = self.panel(certificate_server=certificate, **kwargs)
                mismatch = ('0' if certificate.fingerprint[0] != '0' else '1') + certificate.fingerprint[1:]
                server.panel.tls_fingerprint = mismatch
                with PanelClient(server.panel) as rejected, self.assertRaises(PanelAPIError):
                    if operation == 'upload':
                        rejected.upload_file('/x', 'a.bin', io.BytesIO(b'abc'))
                    else:
                        rejected.file_sha256('/x/a.bin', 10)
                self.assertEqual(server.received, [])
                if operation == 'upload':
                    self.assertIs(client.upload_file('/x', 'a.bin', io.BytesIO(b'abc'))['status'], True)
                else:
                    self.assertEqual(client.file_sha256('/x/a.bin', 10)['size'], 3)
                self.assertEqual(len(server.received), 1)

    def test_sha256_download_keeps_auth_in_get_body_and_hashes_binary(self):
        content = bytes(range(256)) * 500
        server, client = self.panel(body=content, content_type='application/octet-stream',
                                    disposition='attachment; filename="a.bin"')
        result = client.file_sha256('/www/wwwroot/a.bin', len(content))
        self.assertEqual(result, {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)})
        self.assertEqual(server.failures, [])
        self.assertEqual(server.received[0][:4], ('GET', {'filename': ['/www/wwwroot/a.bin']}, {}, []))

    def test_sha256_accepts_official_image_types_and_rfc5987_chinese_filenames(self):
        for name, content_type in [('a.png', 'image/png'), ('a.gif', 'image/gif'),
                                   ('a.jpeg', 'image/jpeg'), ('a.jpg', 'image/jpeg'),
                                   ('报表 一.bin', 'application/octet-stream')]:
            with self.subTest(name=name):
                disposition = ('attachment; filename="a.bin"' if name == '报表 一.bin'
                               else 'attachment; filename=' + name)
                if name == '报表 一.bin':
                    disposition = "attachment; filename=\" .bin\"; filename*=UTF-8''" + quote(name)
                _, client = self.panel(body=b'\xff\x00data', content_type=content_type, disposition=disposition)
                self.assertEqual(client.file_sha256('/x/' + name, 20)['size'], 6)

    def test_sha256_rejects_unsafe_paths_before_delivery(self):
        server, client = self.panel()
        for path in ('relative', '//x/a', '/x//a', '/x/../a', '/x/./a', '/x/a/', '/x/a\\b',
                     '/x/a|/etc/passwd', '/x/a&#46;', '/x/a\n', '/x/\ud800', '/'):
            with self.subTest(path=path), self.assertRaises(PanelAPIError):
                client.file_sha256(path, 100)
        for maximum in (0, -1, True, 20 * 1024 * 1024 + 1):
            with self.subTest(maximum=maximum), self.assertRaises(PanelAPIError):
                client.file_sha256('/x/a', maximum)
        self.assertEqual(server.received, [])

    def test_sha256_rejects_login_types_mismatched_or_ambiguous_dispositions(self):
        cases = [('text/html', 'attachment; filename=a.bin'),
                 ('application/json', 'attachment; filename=a.bin'),
                 ('image/png', 'attachment; filename=a.bin'),
                 ('application/octet-stream', None),
                 ('application/octet-stream', 'inline; filename=a.bin'),
                 ('application/octet-stream', 'attachment; filename=other.bin'),
                 ('application/octet-stream', 'attachment; filename=a.bin; filename=other.bin'),
                 ('application/octet-stream', "attachment; filename=a.bin; filename*=UTF-8''other.bin"),
                 ('application/octet-stream', "attachment; filename*=UTF-8''%ZZ.bin"),
                 ('application/octet-stream', 'attachment; filename="../a.bin"')]
        for content_type, disposition in cases:
            with self.subTest(content_type=content_type, disposition=disposition):
                _, client = self.panel(body=b'raw-secret', content_type=content_type, disposition=disposition)
                with self.assertRaises(PanelAPIError) as raised:
                    client.file_sha256('/x/a.bin', 100)
                self.assertFalse(raised.exception.uncertain)
                self.assertFalse('raw-secret' in str(raised.exception))

    def test_sha256_download_is_bounded_with_and_without_content_length(self):
        for no_length in (False, True):
            with self.subTest(no_length=no_length):
                _, client = self.panel(body=b'x' * 1025, content_type='application/octet-stream',
                                       disposition='attachment; filename=a.bin', no_length=no_length)
                with self.assertRaises(PanelAPIError):
                    client.file_sha256('/x/a.bin', 1024)

    def test_sha256_incomplete_download_fails_with_a_sanitized_error(self):
        _, client = self.panel(body=b'raw-secret', content_type='application/octet-stream',
                               disposition='attachment; filename=a.bin', declared_length=100)
        with self.assertRaises(PanelAPIError) as raised:
            client.file_sha256('/x/a.bin', 1024)
        self.assertFalse('raw-secret' in str(raised.exception))

    def test_sha256_download_redirect_and_total_deadline_are_enforced(self):
        target, _ = self.panel()
        source, client = self.panel(status=302, headers={'Location': target.panel.api_base_url + '/download'})
        with self.assertRaises(PanelAPIError):
            client.file_sha256('/x/a.bin', 1024)
        self.assertEqual(len(source.received), 1)
        self.assertEqual(target.received, [])
        for phase in ('headers', 'body'):
            with self.subTest(phase=phase):
                _, client = self.panel(body=b'x' * 60, content_type='application/octet-stream',
                                       disposition='attachment; filename=a.bin', trickle=phase)
                started = time.monotonic()
                with self.assertRaises(PanelAPIError):
                    client.file_sha256('/x/a.bin', 100, timeout=(0.05, 0.05))
                self.assertLess(time.monotonic() - started, 0.4)

    def test_closed_client_and_invalid_timeouts_are_rejected_before_http(self):
        server, client = self.panel()
        for timeout in (0, -1, float('inf'), (1, None), (1, float('nan'))):
            with self.subTest(timeout=timeout), self.assertRaises(PanelAPIError):
                client.upload_file('/x', 'a', io.BytesIO(b'a'), timeout=timeout)
            with self.assertRaises(PanelAPIError):
                client.file_sha256('/x/a', 10, timeout=timeout)
        client.close()
        with self.assertRaises(PanelAPIError):
            client.upload_file('/x', 'a', io.BytesIO(b'a'))
        with self.assertRaises(PanelAPIError):
            client.file_sha256('/x/a', 10)
        self.assertEqual(server.received, [])
