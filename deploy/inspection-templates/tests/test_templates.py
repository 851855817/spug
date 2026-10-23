import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / (name + '.py')
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load('common')


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(common, 'Safety helpers must exist before import')

    def test_domain_rejects_injection_urls_ips_and_private_names(self):
        for bad in ('', 'https://example.com', 'a/b', 'x\r\nHost:x', 'a;id',
                    '127.0.0.1', '::1', 'localhost', 'a.local', '-a.com', 'a..com',
                    'x:443', 'user@example.com', 'a.com?x=secret', 'a.internal'):
            with self.subTest(bad=bad), self.assertRaises(common.CheckError):
                common.domain_name(bad)
        self.assertEqual(common.domain_name('Example.COM'), 'example.com')

    def test_mixed_private_and_public_dns_is_rejected(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))
                   for ip in ('93.184.216.34', '127.0.0.1')]
        with patch.object(common, 'resolve_dns', return_value=answers):
            with self.assertRaises(common.CheckError):
                common.public_addresses('example.com')

    def test_special_addresses_rejected_even_with_old_global_classification(self):
        for address in ('192.0.0.8', '192.88.99.1', 'fec0::1', 'feff::1',
                        '2002:7f00:1::1', '2001::1', '3fff::1',
                        '64:ff9b::7f00:1', '64:ff9b:1::1', '::ffff:127.0.0.1',
                        '127.0.0.1', '10.0.0.1', '100.64.0.1', '169.254.169.254',
                        '224.0.0.1', 'fe80::1'):
            real = common.ipaddress.ip_address(address)
            legacy = MagicMock(wraps=real)
            legacy.version = real.version
            legacy.is_global = True
            legacy.is_reserved = False
            legacy.is_multicast = real.is_multicast
            legacy.is_unspecified = real.is_unspecified
            legacy.ipv4_mapped = getattr(real, 'ipv4_mapped', None)
            legacy.__int__.return_value = int(real)
            legacy._version = real.version
            legacy._ip = int(real)
            family = socket.AF_INET6 if real.version == 6 else socket.AF_INET
            answers = [(family, socket.SOCK_STREAM, 6, '', (address, 443))]
            with self.subTest(address=address), \
                    patch.object(common, 'resolve_dns', return_value=answers), \
                    patch.object(common.ipaddress, 'ip_address', return_value=legacy):
                with self.assertRaises(common.CheckError):
                    common.public_addresses('example.com')

    def test_tls_pins_validated_ip_and_verifies_hostname(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        raw = MagicMock()
        context = MagicMock()
        with patch.object(common, 'resolve_dns', return_value=answers) as dns, \
                patch.object(common.socket, 'socket', return_value=raw), \
                patch.object(common.ssl, 'create_default_context', return_value=context):
            common.connect_tls('example.com')
        self.assertEqual(dns.call_count, 1)
        raw.connect.assert_called_once_with(('93.184.216.34', 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname='example.com')

    def test_tls_errors_close_socket_and_are_not_downgraded(self):
        raw = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = common.ssl.SSLCertVerificationError('bad cert')
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch.object(common, 'resolve_dns', return_value=answers), \
                patch.object(common.socket, 'socket', return_value=raw), \
                patch.object(common.ssl, 'create_default_context', return_value=context):
            with self.assertRaises(common.ssl.SSLCertVerificationError):
                common.connect_tls('example.com')
        raw.close.assert_called_once()

    def test_files_are_bounded_and_not_links(self):
        with tempfile.TemporaryDirectory() as temp:
            small = Path(temp) / 'ok.ini'
            small.write_bytes(b'display_errors=Off\n')
            self.assertEqual(common.read_text(str(small)), 'display_errors=Off\n')
            with self.assertRaises(common.CheckError):
                common.read_text(str(small), limit=4)
            with self.assertRaises(common.CheckError):
                common.read_text(str(Path(temp)))
            link = Path(temp) / 'link.ini'
            try:
                link.symlink_to(small)
            except OSError:
                self.skipTest('Windows symlink permission unavailable; Linux CI covers it')
            with self.assertRaises((common.CheckError, OSError)):
                common.read_text(str(link))

    @unittest.skipUnless(os.name == 'posix', 'Linux FIFO/dir_fd coverage')
    def test_fifo_and_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / 'fifo'
            os.mkfifo(str(fifo))
            with self.assertRaises(common.CheckError):
                common.read_text(str(fifo))
            (Path(temp) / 'real').mkdir()
            (Path(temp) / 'real' / 'file').write_text('secret')
            (Path(temp) / 'alias').symlink_to(Path(temp) / 'real', target_is_directory=True)
            with self.assertRaises((common.CheckError, OSError)):
                common.read_text(str(Path(temp) / 'alias' / 'file'))

    def test_php_params_cannot_be_paths_or_commands(self):
        for bad in ('../../etc', '74;id', '$(id)', '9.9', 'all\n74'):
            with patch.dict(os.environ, {'_SPUG_PHP_VERSION': bad}):
                with self.assertRaises(common.CheckError):
                    common.php_branches()
        with patch.dict(os.environ, {'_SPUG_PHP_VERSION': '7.4'}):
            self.assertEqual(common.php_branches(), ['74'])

    def test_terminal_controls_removed_and_output_bounded(self):
        text = common.clean('x\x1b[31m\x00\u202ey\n' + 'a' * 10000, 80)
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\u202e', text)
        self.assertLessEqual(len(text), 100)

    @unittest.skipUnless(os.name == 'posix', 'Linux subprocess selector coverage')
    def test_commands_have_timeout_output_cap_and_no_shell(self):
        with self.assertRaises(common.CheckError):
            common.run(['/bin/sh', '-c', 'sleep 2'], timeout=0.05)
        with self.assertRaises(common.CheckError):
            common.run(['/usr/bin/yes'], limit=1024)
        self.assertEqual(common.run(['/bin/echo', '$(id);literal']).strip(), '$(id);literal')
        with self.assertRaises(common.CheckError):
            common.run(['/does/not/exist'])


class BundleTests(unittest.TestCase):
    def test_shell_bundle_contains_eight_native_shell_templates(self):
        build = load('build')
        bundle = build.build_bundle('shell-manifest.json')
        self.assertEqual(len(bundle['templates']), 8)
        for item in bundle['templates']:
            self.assertEqual(item['interpreter'], 'sh')
            self.assertEqual(item['host_ids'], [])
            self.assertIn('timeout', item['body'])
            self.assertNotIn('python', item['body'].lower())
            self.assertIn('https://github.com/', item['body'])

    @unittest.skipUnless(os.name == 'posix', 'Native Linux Shell fixture tests')
    def test_shell_bad_php_parameter_is_rejected_without_execution(self):
        import subprocess
        build = load('build')
        bundle = build.build_bundle('shell-manifest.json')
        body = next(x['body'] for x in bundle['templates'] if x['key'] == 'php_versions')
        for value in ('../../etc', '74; touch /tmp/SPUG_MUST_NOT_RUN', '$(id)', 'all\n74'):
            result = subprocess.run(['/bin/sh'], input=body, text=True,
                                    env={'_SPUG_PHP_VERSION': value}, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('PHP_VERSION', result.stdout)

    @unittest.skipUnless(os.name == 'posix', 'Native Linux Shell fixture tests')
    def test_shell_query_failure_and_output_limits_are_explicit(self):
        import subprocess
        common_shell = (ROOT / 'shell/common.sh').read_text(encoding='utf-8')
        bad = subprocess.run(['/bin/bash'], input=common_shell +
                             '\nrun 5 /bin/sh -c \'echo SECRET >&2; exit 7\'\n',
                             text=True, capture_output=True, timeout=10)
        self.assertEqual(bad.returncode, 2)
        self.assertIn('exit 7', bad.stdout)
        self.assertNotIn('SECRET', bad.stdout + bad.stderr)
        large = subprocess.run(['/bin/bash'], input=common_shell + '\nrun 5 seq 100000\n',
                               text=True, capture_output=True, timeout=10)
        self.assertLessEqual(len(large.stdout), 33000)
        self.assertLessEqual(len([x for x in large.stdout.splitlines() if x.isdigit()]), 5)

    @unittest.skipUnless(os.name == 'posix', 'Native Linux Shell fixture tests')
    def test_shell_hung_query_is_terminated(self):
        import subprocess
        import time
        common_shell = (ROOT / 'shell/common.sh').read_text(encoding='utf-8')
        started = time.monotonic()
        result = subprocess.run(['/bin/bash'], input=common_shell + '\nrun 5 sleep 20\n',
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('timed out', result.stdout)
        self.assertLess(time.monotonic() - started, 9)

    def test_import_is_idempotent_and_refuses_user_edits(self):
        importer = load('import_templates')
        self.assertIsNotNone(importer, 'Create-only import planning must exist')
        item = {'name': 'example', 'type': 'test', 'desc': 'read only',
                'body': 'print(1)', 'interpreter': 'python', 'host_ids': [], 'parameters': []}
        self.assertEqual(importer.plan_import([], [item]), ([item], []))
        self.assertEqual(importer.plan_import([dict(item)], [item]), ([], ['example']))
        modified = dict(item, body='user changed this')
        with self.assertRaises(ValueError):
            importer.plan_import([modified], [item])
        with self.assertRaises(ValueError):
            importer.plan_import([dict(item), dict(item)], [item])

    def test_manifest_builds_twelve_independent_python_templates(self):
        build = load('build')
        self.assertIsNotNone(build, 'Bundle builder must exist')
        bundle = build.build_bundle()
        self.assertEqual(len(bundle['templates']), 12)
        self.assertEqual(len({x['name'] for x in bundle['templates']}), 12)
        for item in bundle['templates']:
            self.assertEqual(item['host_ids'], [])
            self.assertEqual(item['interpreter'], 'python')
            self.assertLessEqual(len(item['desc']), 255)
            self.assertNotIn('\nSPUG_PY_EOF\n', item['body'])
            self.assertIn('https://github.com/', item['body'])
            tree = ast.parse(item['body'])
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self.assertNotIn(getattr(node.func, 'id', ''), ('eval', 'exec'))
            self.assertIn('def main()', item['body'])

    def test_php_config_never_prints_unlisted_secrets(self):
        check = load('checks/php_config')
        self.assertIsNotNone(check)
        fixture = ('display_errors=On\npassword=SECRET\n'
                   'session.cookie_secure=1\nauto_prepend_file=/private/token.php\n')
        check.__dict__.update(common.__dict__)
        out = io.StringIO()
        with patch.dict(check.__dict__, read_text=lambda *a, **k: fixture,
                        installed_branches=lambda: ['74']), contextlib.redirect_stdout(out):
            check.main()
        self.assertNotIn('SECRET', out.getvalue())
        self.assertNotIn('token.php', out.getvalue())
        self.assertIn('display_errors=On', out.getvalue())

    def test_website_does_not_follow_redirects_or_read_body(self):
        check = load('checks/website')
        self.assertIsNotNone(check)
        check.__dict__.update(common.__dict__)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [bytes([x]) for x in b'HTTP/1.1 302 Found\r\n']
        out = io.StringIO()
        with patch.dict(os.environ, {'_SPUG_DOMAIN': 'example.com'}), \
                patch.dict(check.__dict__, connect_tls=lambda domain: connection), \
                contextlib.redirect_stdout(out):
            self.assertEqual(check.main(), 0)
        self.assertEqual(connection.sendall.call_count, 1)
        request = connection.sendall.call_args.args[0]
        self.assertTrue(request.startswith(b'HEAD / HTTP/1.1\r\n'))
        self.assertIn('not followed', out.getvalue())

    def test_oversized_http_status_is_rejected(self):
        check = load('checks/website')
        self.assertIsNotNone(check)
        check.__dict__.update(common.__dict__)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b'x'
        with patch.dict(os.environ, {'_SPUG_DOMAIN': 'example.com'}), \
                patch.dict(check.__dict__, connect_tls=lambda domain: connection):
            with self.assertRaises(common.CheckError):
                check.main()
        self.assertEqual(connection.recv.call_count, 1024)

    def test_http_interim_responses_are_not_reported_as_success(self):
        for response in (b'HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 503 Down\r\n',
                         b'HTTP/1.1 103 Early Hints\r\n\r\nHTTP/1.1 200 OK\r\n',
                         b'HTTP/1.1 100 Continue\r\n'):
            check = load('checks/website')
            check.__dict__.update(common.__dict__)
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.recv.side_effect = [bytes([x]) for x in response]
            with patch.dict(os.environ, {'_SPUG_DOMAIN': 'example.com'}), \
                    patch.dict(check.__dict__, connect_tls=lambda domain: connection):
                with self.assertRaises(common.CheckError):
                    check.main()


if __name__ == '__main__':
    unittest.main()
