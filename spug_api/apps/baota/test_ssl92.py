"""9.2 source-protocol fixture: GetSSL does not resolve configured cert paths.

The official package is evidence only and is never imported/executed by these tests.
This fixture intentionally does not inherit the modern PanelFixture.
"""
import copy
import datetime
import hashlib
import json
import posixpath
from unittest import TestCase
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import resources
from .client import PanelAPIError, PanelClient
from .operations.certificates import ApplyCertificate, RenewCertificate, certificate_info


SITE = 'example.com'
CERT = '/www/server/panel/vhost/cert/example.com/fullchain.pem'
KEY = '/www/server/panel/vhost/cert/example.com/privkey.pem'
INDEX = 'a' * 32


def public_certificate(days, serial):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SITE)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(serial).not_valid_before(now - datetime.timedelta(days=2))
            .not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(SITE)]), critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode('ascii')


def nginx_config(certificate=True, includes=True, quote=''):
    # panelSite.py:272-329 + SetSSLConf:2117-2131, literal source protocol shape.
    extra = '''
    include /www/server/panel/vhost/nginx/well-known/example.com.conf;
    include /www/server/panel/vhost/nginx/extension/example.com/*.conf;
    include enable-php-74.conf;
    include /www/server/panel/vhost/rewrite/example.com.conf;
''' if includes else ''
    ssl = ('listen 443 ssl;\n    ssl_certificate ' + quote + CERT + quote + ';\n'
           '    ssl_certificate_key ' + quote + KEY + quote + ';') if certificate else ''
    return ('server\n{\n    listen 80;\n    server_name example.com;\n    ' + ssl + extra
            + '\n    #error_page 404/404.html;\n    location ~ /\\.well-known { allow all; }\n}\n')


def apache_config(quote='', certificate=True):
    # panelSite.py:161-185 and :2266-2308 generate HTTP + SSL VirtualHosts.
    http = '''<VirtualHost *:80>
    ServerName example.com
    ServerAlias example.com
    IncludeOptional /www/server/panel/vhost/apache/extension/example.com/*.conf
</VirtualHost>
'''
    ssl = ('''<VirtualHost *:443>
    ServerName SSL.example.com
    ServerAlias example.com
    SSLEngine On
    SSLCertificateFile ''' + quote + CERT + quote + '''
    SSLCertificateKeyFile ''' + quote + KEY + quote + '''
</VirtualHost>
''')
    return http + (ssl if certificate else '')


class Source92SSLFixture:
    """Reviewed GetSSL canonical read, ACME auto-sync, fixed download contracts."""
    def __init__(self, old, new, *, server='nginx', configuration=None, enabled=True):
        self.info = {'version': '9.2.0', 'system': 'Debian GNU/Linux 12', 'cpuNum': 2, 'memTotal': 2048}
        self.server = server
        self.site = {'id': 1, 'name': SITE, 'path': '/www/wwwroot/example.com',
                     'project_type': 'PHP', 'php_version': '7.4', 'status': '1'}
        self.configuration = configuration if configuration is not None else (
            apache_config(certificate=enabled) if server == 'apache' else nginx_config(enabled))
        self.canonical, self.new = old if enabled else '', new
        self.orders = ([{'index': INDEX, 'domains': [SITE], 'auth_type': 'http',
                        'auth_to': self.site['path'], 'status': 'valid',
                        'save_path': 'vhost/letsencrypt/' + SITE, 'ca': False}] if enabled else [])
        self.issued = old if enabled else ''
        self.calls, self.downloads, self.directory_writes = [], [], []
        self.shrink_renewal = False
        self.transport_error = None
        self.root_missing = False
        self.after_mutation_error = None

    def read_site_web_config(self, server, name):
        self.downloads.append('/www/server/panel/vhost/' + server + '/' + name + '.conf')
        if self.transport_error:
            raise self.transport_error
        if self.configuration is None:
            raise PanelAPIError('missing fixed file')
        return self.configuration

    def read_site_public_certificate(self, name):
        self.downloads.append('/www/server/panel/vhost/cert/' + name + '/fullchain.pem')
        if not self.canonical:
            raise PanelAPIError('missing certificate')
        return self.canonical

    def read_issued_certificate(self, domain):
        self.downloads.append('/www/server/panel/vhost/letsencrypt/' + domain + '/fullchain.pem')
        return self.issued

    def request(self, path, action=None, params=None, mutating=False, **kwargs):
        p = copy.deepcopy(params or {})
        self.calls.append((path, action, p, mutating))
        if self.transport_error:
            raise self.transport_error
        if action == 'GetSystemTotal': return copy.deepcopy(self.info)
        if action == 'GetConcifInfo': return {'webserver': self.server, 'mysql_root': 'DO_NOT_STORE'}
        if action == 'getData':
            data = {'sites': [self.site], 'domain': [{'id': 1, 'pid': 1, 'name': SITE, 'port': 80}]}[p['table']]
            return {'data': copy.deepcopy(data)}
        if action == 'get_file_attribute':
            if self.root_missing: return {'status': False, 'msg': 'missing'}
            filename = p['filename']
            return {'name': posixpath.basename(filename), 'path': posixpath.dirname(filename),
                    'is_dir': True, 'is_link': False}
        if action == 'GetSiteRunPath':
            if self.root_missing: self.directory_writes.append(self.site['path'])
            return {'runPath': '/'}
        if action == 'get_orders': return copy.deepcopy(self.orders)
        if action == 'GetSSL':
            # 9.2 panelSite.py:2653-2663 reads only canonical/fullchain, then raw
            # conf.find at :2686-2699; even commented/custom directives enable it.
            needle = 'ssl_certificate' if self.server == 'nginx' else 'SSLCertificateFile'
            enabled = self.configuration is not None and needle in self.configuration
            return {'status': enabled, 'csr': self.canonical, 'key': 'PRIVATE_DO_NOT_STORE',
                    'index': INDEX if self.orders and enabled else '', 'auth_type': 'http', 'type': 1 if enabled else -1}
        if action in ('apply_cert_api', 'renew_cert'):
            self.issued = self.new
            if not self.orders:
                self.orders = [{'index': INDEX, 'domains': json.loads(p['domains']), 'auth_type': p['auth_type'],
                                'auth_to': p['auth_to'], 'status': 'valid',
                                'save_path': 'vhost/letsencrypt/' + SITE, 'ca': False}]
            if self.canonical: self.canonical = self.new  # sub_all_cert default same-SAN target.
            if self.shrink_renewal: self.orders[0]['domains'] = ['changed.example.com']
            if self.after_mutation_error: raise self.after_mutation_error
            return {'status': True, 'private_key': 'PRIVATE_DO_NOT_STORE', 'index': INDEX}
        if action == 'SetCertToSite':
            self.canonical = self.new
            self.configuration = apache_config() if self.server == 'apache' else nginx_config(True)
            if self.after_mutation_error: raise self.after_mutation_error
            return {'status': True}
        raise AssertionError((path, action, p, mutating))

    @property
    def mutations(self):
        return [row for row in self.calls if row[3]]


class SSL92Tests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.old, cls.new = public_certificate(10, 9201), public_certificate(90, 9202)

    def setUp(self):
        # Isolate the SSL protocol while the integration owner changes version gates.
        self.support = patch.object(resources, 'SUPPORTED_VERSIONS', resources.SUPPORTED_VERSIONS | {'9.2.0'})
        self.support.start()
        self.addCleanup(self.support.stop)

    def preflight(self, fixture, operation=None):
        return (operation or ApplyCertificate()).preflight(fixture, {'sites': [1]})['items'][0]

    def test_canonical_getssl_cannot_verify_custom_configured_certificate(self):
        f = Source92SSLFixture(self.old, self.new, configuration=nginx_config().replace(CERT, '/custom/old.pem'))
        item = self.preflight(f)
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(f.mutations, [])
        self.assertFalse(any('/custom/' in filename for filename in f.downloads))

    def test_default_includes_only_verify_main_config_and_auto_sync_avoids_deploy(self):
        f = Source92SSLFixture(self.old, self.new)
        op = ApplyCertificate()
        item = self.preflight(f, op)
        self.assertEqual(item['state'], 'ready')
        outcome = op.execute(f, item)
        self.assertEqual(outcome['state'], 'success')
        self.assertTrue(outcome['evidence']['selected_site_main_configuration_verified'])
        self.assertFalse(outcome['evidence']['selected_site_configuration_verified'])
        self.assertFalse(outcome['evidence']['included_configuration_verified'])
        self.assertFalse(outcome['evidence']['live_tls_verified'])
        self.assertFalse(outcome['evidence']['panel_sync_outcome_verified'])
        self.assertEqual([c[1] for c in f.mutations], ['apply_cert_api'])
        self.assertNotIn('PRIVATE', json.dumps(item) + json.dumps(outcome))

    def test_no_include_and_quoted_canonical_path_can_verify_main_config(self):
        for quote in ('', "'", '"'):
            with self.subTest(quote=quote):
                f = Source92SSLFixture(self.old, self.new, configuration=nginx_config(includes=False, quote=quote))
                op = ApplyCertificate()
                outcome = op.execute(f, self.preflight(f, op))
                self.assertEqual(outcome['state'], 'success')
                self.assertTrue(outcome['evidence']['selected_site_configuration_verified'])
                self.assertTrue(outcome['evidence']['included_configuration_verified'])

    def test_comments_cannot_supply_certificate_evidence(self):
        invalid = [nginx_config(False) + '\n# ssl_certificate ' + CERT + ';\n',
                   nginx_config().replace('ssl_certificate ' + CERT, '# ssl_certificate ' + CERT),
                   nginx_config().replace(CERT, '/custom/old.pem') + '\n# ssl_certificate ' + CERT + ';\n']
        for conf in invalid:
            with self.subTest(conf=conf):
                f = Source92SSLFixture(self.old, self.new, configuration=conf)
                self.assertEqual(self.preflight(f)['state'], 'failed')
                self.assertEqual(f.mutations, [])

    def test_unknown_include_multiple_servers_and_path_conflicts_fail_closed(self):
        invalid = [nginx_config() + '\ninclude /etc/nginx/custom.conf;\n',
                   nginx_config() + nginx_config(),
                   nginx_config().replace('ssl_certificate_key', 'ssl_certificate ' + CERT + ';\n ssl_certificate_key'),
                   nginx_config().replace(CERT, '"' + CERT + '" /tmp/other.pem'),
                   nginx_config().replace(CERT, '"" ' + CERT),
                   nginx_config().replace(CERT, '"' + CERT + '"""'),
                   nginx_config().replace(CERT, '$certificate'),
                   nginx_config().replace('listen 443 ssl;', 'listen 443 ssl;\n ssl_conf_command Certificate /custom/override.pem;'),
                   nginx_config().replace('listen 443 ssl;', 'listen 443 ssl;\n ssl_conf_command PrivateKey /custom/key.pem;'),
                   nginx_config().replace('listen 443 ssl;', 'listen 443 ssl;\n ssl_certificate_by_lua_block { return; }'),
                   nginx_config().replace(CERT, CERT + '/../fullchain.pem')]
        for conf in invalid:
            with self.subTest(conf=conf):
                f = Source92SSLFixture(self.old, self.new, configuration=conf)
                self.assertEqual(self.preflight(f)['state'], 'failed')
                self.assertEqual(f.mutations, [])

    def test_apache_standard_http_tls_vhosts_and_quoted_path(self):
        for quote in ('', "'", '"'):
            with self.subTest(quote=quote):
                f = Source92SSLFixture(self.old, self.new, server='apache', configuration=apache_config(quote))
                op = ApplyCertificate()
                item = self.preflight(f, op)
                self.assertEqual(item['state'], 'ready')
                outcome = op.execute(f, item)
                self.assertEqual(outcome['state'], 'success')
                self.assertTrue(outcome['evidence']['selected_site_main_configuration_verified'])
                self.assertFalse(outcome['evidence']['selected_site_configuration_verified'])
        f = Source92SSLFixture(self.old, self.new, server='apache', configuration=apache_config().replace(CERT, '/custom/cert.pem'))
        self.assertEqual(self.preflight(f)['state'], 'failed')

    def test_apache_tls_vhost_must_itself_name_the_selected_site(self):
        conf = apache_config().replace('ServerName SSL.example.com', 'ServerName unrelated.example.net')
        conf = conf.replace('SSLEngine On', 'ServerAlias unrelated.example.net\n    SSLEngine On')
        # Keep site name in HTTP host only, not the TLS host.
        head, tail = conf.split('<VirtualHost *:443>')
        conf = head + '<VirtualHost *:443>' + tail.replace('ServerAlias example.com', 'ServerAlias unrelated.example.net')
        f = Source92SSLFixture(self.old, self.new, server='apache', configuration=conf)
        self.assertEqual(self.preflight(f)['state'], 'failed')
        self.assertEqual(f.mutations, [])

    def test_apache_openssl_configuration_overrides_are_rejected(self):
        for directive in ('SSLOpenSSLConfCmd Certificate /custom/override.pem',
                          'SSLOpenSSLConfCmd PrivateKey /custom/key.pem',
                          'SSLOpenSSLConfCmd Protocol -TLSv1',
                          'sslopensslconfcmd Certificate /custom/override.pem'):
            with self.subTest(directive=directive):
                f = Source92SSLFixture(self.old, self.new, server='apache',
                    configuration=apache_config().replace('SSLEngine On', 'SSLEngine On\n    ' + directive))
                self.assertEqual(self.preflight(f)['state'], 'failed')
                self.assertEqual(f.mutations, [])
                self.assertFalse(any('/custom/' in filename for filename in f.downloads))

    def test_apache_sensitive_arguments_never_normalize_backslash_paths(self):
        configurations = [apache_config().replace(CERT, CERT.replace('example', 'exam\\ple')),
                          apache_config().replace(KEY, KEY.replace('privkey', 'pri\\vkey')),
                          apache_config().replace('/extension/example.com/', '/extension/exam\\ple.com/'),
                          apache_config().replace('ServerAlias example.com', 'ServerAlias exam\\ple.com')]
        for conf in configurations:
            with self.subTest(conf=conf):
                f = Source92SSLFixture(self.old, self.new, server='apache', configuration=conf)
                self.assertEqual(self.preflight(f)['state'], 'failed')
                self.assertEqual(f.mutations, [])

    def test_inventory_getssl_does_not_assert_unselected_project_main_config(self):
        f = Source92SSLFixture(self.old, self.new)
        request = f.request
        def response(path, action=None, params=None, **kwargs):
            data = request(path, action, params, **kwargs)
            if action == 'getData' and params['table'] == 'sites':
                data['data'].append({**f.site, 'id': 2, 'name': 'node.example.net', 'project_type': 'Node'})
            return data
        f.request = response
        item = self.preflight(f)
        self.assertEqual(item['state'], 'ready')
        self.assertFalse(any('node.example.net.conf' in name for name in f.downloads))
        self.assertEqual(item['before']['ssl_sync_inventory']['coverage'], 'registered_sites_GetSSL_only')

    def test_main_config_drift_blocks_dispatch_and_readonly_recovery(self):
        f = Source92SSLFixture(self.old, self.new)
        op = ApplyCertificate()
        item = self.preflight(f, op)
        f.configuration += '\n# operator changed main config\n'
        self.assertEqual(op.execute(f, item)['state'], 'failed')
        self.assertEqual(f.mutations, [])
        f = Source92SSLFixture(self.old, self.new)
        item = self.preflight(f, op)
        f.issued, f.canonical = self.new, self.new
        f.configuration += '\n# operator changed main config\n'
        outcome = op.reconcile(f, item, {'phase': 'issue_and_sync', 'index': INDEX, 'ambiguous': True})
        self.assertEqual(outcome['state'], 'uncertain')
        self.assertFalse(outcome['evidence'].get('selected_site_main_configuration_verified'))
        self.assertEqual(f.mutations, [])

    def test_getssl_and_public_download_certificate_must_match(self):
        f = Source92SSLFixture(self.old, self.new)
        f.read_site_public_certificate = lambda name: self.new
        self.assertEqual(self.preflight(f)['state'], 'failed')
        self.assertEqual(f.mutations, [])

    def test_config_change_during_public_certificate_download_fails_snapshot(self):
        f = Source92SSLFixture(self.old, self.new)
        def changed(name):
            f.configuration = f.configuration.replace(CERT, '/custom/changed.pem')
            return self.old
        f.read_site_public_certificate = changed
        self.assertEqual(self.preflight(f)['state'], 'failed')
        self.assertEqual(f.mutations, [])
        self.assertFalse(any('/custom/' in name for name in f.downloads))

    def test_first_issuance_requires_separate_deploy_and_reconcile_never_mutates(self):
        f = Source92SSLFixture(self.old, self.new, enabled=False)
        op = ApplyCertificate()
        item = self.preflight(f, op)
        self.assertEqual(item['state'], 'ready')
        issued = op.execute(f, item)
        self.assertEqual(issued['state'], 'remote')
        self.assertEqual(issued['remote']['next_action'], 'deploy')
        self.assertEqual([c[1] for c in f.mutations], ['apply_cert_api'])
        checked = op.reconcile(f, item, issued['remote'])
        self.assertEqual(len(f.mutations), 1)
        deployed = op.dispatch(f, item, 'deploy', checked['remote'])
        self.assertEqual(deployed['state'], 'success')
        self.assertEqual([c[1] for c in f.mutations], ['apply_cert_api', 'SetCertToSite'])

    def test_apache_first_issuance_and_independent_deploy_readback(self):
        f = Source92SSLFixture(self.old, self.new, server='apache', enabled=False,
                              configuration=apache_config(certificate=False))
        op = ApplyCertificate()
        item = self.preflight(f, op)
        self.assertEqual(item['state'], 'ready')
        issued = op.execute(f, item)
        self.assertEqual(issued['state'], 'remote')
        self.assertEqual(issued['remote']['next_action'], 'deploy')
        checked = op.reconcile(f, item, issued['remote'])
        self.assertEqual([c[1] for c in f.mutations], ['apply_cert_api'])
        deployed = op.dispatch(f, item, 'deploy', checked['remote'])
        self.assertEqual(deployed['state'], 'success')
        self.assertIn('SSLCertificateFile', f.configuration)
        self.assertTrue(deployed['evidence']['selected_site_main_configuration_verified'])
        self.assertFalse(deployed['evidence']['selected_site_configuration_verified'])
        self.assertFalse(deployed['evidence']['live_tls_verified'])
        self.assertEqual([c[1] for c in f.mutations], ['apply_cert_api', 'SetCertToSite'])

    def test_missing_config_and_root_are_not_created(self):
        f = Source92SSLFixture(self.old, self.new)
        f.configuration = None
        self.assertEqual(self.preflight(f)['state'], 'failed')
        self.assertEqual(f.directory_writes, [])
        f = Source92SSLFixture(self.old, self.new)
        f.root_missing = True
        self.assertEqual(self.preflight(f)['state'], 'failed')
        self.assertEqual(f.directory_writes, [])
        self.assertEqual(f.mutations, [])

    def test_renewal_san_change_does_not_reissue_or_claim_success(self):
        f = Source92SSLFixture(self.old, self.new)
        f.shrink_renewal = True
        op = RenewCertificate()
        item = self.preflight(f, op)
        outcome = op.execute(f, item)
        self.assertIn(outcome['state'], ('remote', 'uncertain'))
        op.reconcile(f, item, outcome['remote'])
        self.assertEqual([c[1] for c in f.mutations], ['renew_cert'])

    def test_timeout_and_pinned_tls_failure_preserve_readonly_recovery(self):
        f = Source92SSLFixture(self.old, self.new)
        op = ApplyCertificate()
        item = self.preflight(f, op)
        f.after_mutation_error = PanelAPIError('timeout', uncertain=True)
        outcome = op.execute(f, item)
        self.assertEqual(outcome['state'], 'uncertain')
        self.assertTrue(outcome['remote']['ambiguous'])
        f.transport_error = PanelAPIError('pinned certificate changed')
        outcome = op.reconcile(f, item, outcome['remote'])
        self.assertEqual(outcome['state'], 'uncertain')
        self.assertNotIn('next_action', outcome['remote'])
        self.assertEqual([c[1] for c in f.mutations], ['apply_cert_api'])


class FixedSSLReader92Tests(TestCase):
    def client(self):
        # Exercise actual fixed methods and shared signed transport without a key file.
        c = PanelClient.__new__(PanelClient)
        c._closed, c._key, c.base_url, c.verify = False, 'credential', 'https://panel.invalid:8888', False
        c.session = type('Session', (), {})()
        return c

    def test_fixed_reader_scopes_reject_traversal_and_never_accept_paths(self):
        c = self.client()
        self.assertTrue(callable(getattr(c, 'read_site_web_config', None)))
        self.assertTrue(callable(getattr(c, 'read_site_public_certificate', None)))
        for name in ('../example.com', '/etc/passwd', 'example.com/privkey.pem', '.', '..', 'a\\b', 'a|b', 'a&b'):
            with self.subTest(name=name):
                with self.assertRaises(PanelAPIError): c.read_site_web_config('nginx', name)
                with self.assertRaises(PanelAPIError): c.read_site_public_certificate(name)
        with self.assertRaises(PanelAPIError): c.read_site_web_config('openlitespeed', SITE)

    def test_fixed_download_reuses_body_signature_and_existing_tls_verification(self):
        c = self.client()
        calls = []
        class Response:
            status_code = 200
            def __init__(self, expected):
                self.headers = {'Content-Type': 'application/octet-stream',
                                'Content-Disposition': 'attachment; filename="' + expected + '"'}
            def iter_content(self, chunk_size): return iter([b'public fixture'])
            def close(self): pass
        def get(url, **kwargs):
            calls.append((url, kwargs))
            return Response(posixpath.basename(kwargs['params']['filename']))
        c.session.get = get
        self.assertTrue(callable(getattr(c, 'read_site_web_config', None)))
        self.assertTrue(callable(getattr(c, 'read_site_public_certificate', None)))
        c.read_site_web_config('nginx', SITE)
        c.read_site_web_config('apache', SITE)
        c.read_site_public_certificate(SITE)
        self.assertEqual([v[1]['params']['filename'] for v in calls], [
            '/www/server/panel/vhost/nginx/example.com.conf',
            '/www/server/panel/vhost/apache/example.com.conf', CERT])
        for url, kwargs in calls:
            self.assertEqual(url, c.base_url + '/download')
            self.assertEqual(set(kwargs['params']), {'filename'})
            body = kwargs['data']
            self.assertEqual(body['request_token'], hashlib.md5((body['request_time'] + hashlib.md5(b'credential').hexdigest()).encode('ascii')).hexdigest())
            self.assertIs(kwargs['verify'], c.verify)
            self.assertFalse(kwargs['allow_redirects'])
            self.assertTrue(kwargs['stream'])
