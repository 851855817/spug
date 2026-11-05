import datetime
import io
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
from types import SimpleNamespace
from unittest import TestCase, mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import collectors, remote_probe, tls


class AdapterContractTests(TestCase):
    def test_discovery_and_renewal_contract_exists(self):
        for module, names in ((collectors, ('discover', 'renew_host', 'public_certificate')),
                              (remote_probe, ('scan', 'renew')), (tls, ('check_tls', 'normalize_domain'))):
            for name in names:
                with self.subTest(name=name):
                    self.assertTrue(callable(getattr(module, name, None)), 'missing adapter: ' + name)


def public_pem(domains=('example.com',), expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=40))
            .not_valid_after(now + datetime.timedelta(days=-1 if expired else 50))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(v) for v in domains]), False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode('ascii')


class ProbeTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pem = public_pem()
        self.write('/usr/sbin/nginx', 'fixture nginx executable')
        self.write('/run/nginx.pid', '12345\n')
        self.write('/proc/12345/cmdline', 'nginx: master process /usr/sbin/nginx\x00')
        self.write('/proc/12345/stat', '12345 (nginx) S ' + ' '.join(['1'] * 30))
        patcher = mock.patch.object(remote_probe.Reader, 'link', return_value='/usr/sbin/nginx')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write('/etc/nginx/nginx.conf', 'events {} http { include /etc/nginx/sites-enabled/*; }')
        self.write('/etc/nginx/sites-enabled/example.conf', '''server {
            listen 443 ssl; server_name EXAMPLE.com *.example.com _;
            ssl_certificate /srv/tls/fullchain.pem;
            ssl_certificate_key /srv/tls/private.key;
        }''')
        self.write('/srv/tls/fullchain.pem', self.pem + 'NEVER_READ_AFTER_PUBLIC_CERT')
        self.write('/srv/tls/private.key', 'PRIVATE_KEY_SENTINEL')

    def write(self, path, text):
        target = self.root / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf8')
        return target

    def scan(self):
        return remote_probe.scan(str(self.root))

    def test_nginx_includes_and_certificate_only(self):
        result = self.scan()
        self.assertEqual({x['domain'] for x in result['items']}, {'example.com', '*.example.com'})
        self.assertEqual({x['port'] for x in result['items']}, {443})
        wire = json.dumps(result)
        self.assertNotIn('PRIVATE_KEY_SENTINEL', wire)
        self.assertNotIn('NEVER_READ_AFTER_PUBLIC_CERT', wire)
        self.assertEqual(result['items'][0]['public_pem'], self.pem)
        self.assertTrue(result['warnings'])

    def test_valid_complete_scan_has_no_coverage_warning(self):
        self.write('/etc/nginx/sites-enabled/example.conf', '''server {
listen 443 ssl; server_name example.com;
ssl_certificate /srv/tls/fullchain.pem; ssl_certificate_key /srv/tls/private.key;
}''')
        self.assertEqual(self.scan()['warnings'], [])

    def test_relative_nginx_include_uses_main_configuration_prefix(self):
        self.write('/etc/nginx/sites-enabled/example.conf', 'include snippets/ssl-site.conf;')
        self.write('/etc/nginx/snippets/ssl-site.conf', '''server {
listen 443 ssl; server_name example.com;
ssl_certificate /srv/tls/fullchain.pem; ssl_certificate_key /srv/tls/private.key;
}''')
        self.assertEqual([x['domain'] for x in self.scan()['items']], ['example.com'])

    def test_nginx_static_location_regex_end_anchor_is_not_a_dynamic_certificate(self):
        self.write('/etc/nginx/sites-enabled/example.conf', r'''server {
listen 443 ssl; server_name example.com;
ssl_certificate /srv/tls/fullchain.pem;
ssl_certificate_key /srv/tls/private.key;
location ~ \.(jpg|png)$ { expires 30d; }
location ~* ^/private/.*$ { deny all; }
}''')
        result = self.scan()
        self.assertEqual([(item['domain'], item['port']) for item in result['items']], [('example.com', 443)])
        self.assertEqual(result['warnings'], [])

    def test_probe_reports_known_discovery_cause_without_remote_configuration(self):
        for code in ('configuration_syntax', 'dynamic_configuration', 'include_missing',
                     'include_outside_roots', 'include_cycle', 'include_depth_limit',
                     'file_unreadable', 'non_regular_file', 'configuration_encoding',
                     'file_limit', 'total_limit', 'item_limit', 'certificate_invalid'):
            with self.subTest(code=code), mock.patch.object(remote_probe, 'scan', side_effect=remote_probe.ProbeError(code)), mock.patch.object(
                    remote_probe.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"action":"discover"}'))), mock.patch.object(
                    remote_probe.sys, 'stdout', new_callable=io.StringIO) as output:
                remote_probe.main()
                self.assertEqual(json.loads(output.getvalue()), {'error': code})

    def test_regex_location_does_not_allow_dynamic_certificate_selection(self):
        for directive in ('ssl_certificate $ssl_server_name.pem;',
                          'ssl_certificate_by_lua_block { return nil; }'):
            with self.subTest(directive=directive):
                self.write('/etc/nginx/sites-enabled/example.conf', r'''server {
listen 443 ssl; server_name example.com;
ssl_certificate /srv/tls/fullchain.pem;
location ~ \.(jpg|png)$ { ''' + directive + ' } }')
                with self.assertRaisesRegex(remote_probe.ProbeError, 'dynamic_configuration'):
                    self.scan()

    def test_probe_unknown_exception_does_not_expose_remote_contents(self):
        with mock.patch.object(remote_probe, 'scan', side_effect=RuntimeError('SECRET CONFIGURATION')), mock.patch.object(
                remote_probe.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))), mock.patch.object(
                remote_probe.sys, 'stdout', new_callable=io.StringIO) as output:
            remote_probe.main()
            self.assertEqual(json.loads(output.getvalue()), {'error': 'unsupported'})

    def test_known_discovery_errors_survive_ssh_output_boundary(self):
        channel = mock.Mock()
        channel.recv_ready.side_effect = [True, False]
        channel.recv.return_value = b'{"error":"include_missing"}'
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        with self.assertRaises(collectors.CollectorError) as caught:
            collectors._read_output(channel, collectors.time.monotonic() + 10)
        self.assertEqual(caught.exception.code, 'include_missing')

    def test_baota_default_nginx_source_fixture_expands_fixed_rewrite_root(self):
        from apps.baota.test_ssl92 import nginx_config, CERT
        self.write('/etc/nginx/nginx.conf', '')
        self.write('/www/server/nginx/conf/nginx.conf', '''events {} http {
include /www/server/panel/vhost/nginx/*.conf;
}''')
        self.write('/www/server/panel/vhost/nginx/example.com.conf', nginx_config())
        self.write('/www/server/panel/vhost/nginx/well-known/example.com.conf', '')
        self.write('/www/server/nginx/conf/enable-php-74.conf', '')
        self.write('/www/server/panel/vhost/rewrite/example.com.conf', '')
        self.write(CERT, self.pem)
        snapshot = self.scan()
        self.assertEqual([(item['domain'], item['port']) for item in snapshot['items']], [('example.com', 443)])
        self.assertEqual(snapshot['warnings'], [])

    def test_lua_certificate_selection_never_becomes_renewal_supported(self):
        self.certbot()
        self.write('/etc/nginx/sites-enabled/example.conf', '''server {
listen 443 ssl; server_name example.com;
ssl_certificate /srv/tls/fullchain.pem; ssl_certificate_key /srv/tls/private.key;
ssl_certificate_by_lua_block { return nil; }
}''')
        with self.assertRaises(remote_probe.ProbeError):
            self.scan()

    def test_openssl_conf_cannot_override_observed_certificate(self):
        self.write('/etc/nginx/sites-enabled/example.conf', '''server {
listen 443 ssl; server_name example.com;
ssl_certificate /srv/tls/fullchain.pem; ssl_certificate_key /srv/tls/private.key;
ssl_conf_command Certificate /some/other.pem;
}''')
        with self.assertRaises(remote_probe.ProbeError):
            self.scan()

    def test_unknown_reload_pid_path_blocks_renewal(self):
        self.certbot()
        self.write('/etc/nginx/nginx.conf', '''pid /tmp/unverified.pid;
events {} http { include /etc/nginx/sites-enabled/*; }''')
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])

    def test_nginx_master_must_match_known_binary_before_reload(self):
        self.certbot()
        self.write('/etc/nginx/nginx.conf', '''pid /run/nginx.pid;
events {} http { include /etc/nginx/sites-enabled/*; }''')
        self.write('/run/nginx.pid', '12345\n')
        self.write('/proc/12345/cmdline', 'some unrelated command\x00')
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])

    def test_acme_ca_shell_config_is_not_sourced_or_ignored(self):
        self.test_acme_literal_existing_order_and_dynamic_account_rejection()
        self.write('/root/.acme.sh/account.conf', "ACCOUNT_EMAIL='admin@example.com'\n")
        self.write('/root/.acme.sh/ca/acme-v02.api.letsencrypt.org/directory/ca.conf', "PATH='/tmp/evil'\n")
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])

    def test_plain_http_on_port_443_does_not_claim_tls_configuration(self):
        self.write('/etc/nginx/sites-enabled/example.conf', '''server {
listen 443; server_name example.com; ssl_certificate /srv/tls/fullchain.pem;
}''')
        self.assertEqual(self.scan()['items'], [])

    def test_include_cycle_fails_without_partial_snapshot(self):
        self.write('/etc/nginx/sites-enabled/example.conf', 'include /etc/nginx/nginx.conf;')
        with self.assertRaisesRegex(remote_probe.ProbeError, 'include_cycle'):
            self.scan()

    def test_include_escape_and_dynamic_certificate_fail_closed(self):
        self.write('/etc/nginx/nginx.conf', 'include /tmp/secret.conf;')
        with self.assertRaisesRegex(remote_probe.ProbeError, 'include_outside_roots'):
            self.scan()
        self.write('/etc/nginx/nginx.conf', 'server { listen 443 ssl; server_name example.com; ssl_certificate $secret; }')
        with self.assertRaisesRegex(remote_probe.ProbeError, 'dynamic_configuration'):
            self.scan()

    def test_file_budget_is_failure(self):
        with mock.patch.object(remote_probe, 'MAX_FILES', 1):
            with self.assertRaisesRegex(remote_probe.ProbeError, 'limit'):
                self.scan()

    def test_private_key_in_certificate_file_is_never_returned(self):
        self.write('/srv/tls/fullchain.pem', '-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----\n' + self.pem)
        result = self.scan()
        self.assertEqual(result['items'][0]['public_pem'], '')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_apache_vhost_and_aliases(self):
        self.write('/etc/nginx/nginx.conf', '')
        self.write('/etc/nginx/sites-enabled/example.conf', '')
        self.write('/etc/apache2/apache2.conf', 'IncludeOptional /etc/apache2/sites-enabled/*.conf')
        self.write('/etc/apache2/sites-enabled/site.conf', '''<VirtualHost *:8443>
ServerName example.com
ServerAlias www.example.com
SSLEngine on
SSLCertificateFile /srv/tls/fullchain.pem
SSLCertificateKeyFile /srv/tls/private.key
</VirtualHost>''')
        result = self.scan()
        self.assertEqual({(x['domain'], x['port']) for x in result['items']},
                         {('example.com', 8443), ('www.example.com', 8443)})

    def test_apache_sslengine_is_case_insensitive_and_invalid_value_fails(self):
        self.write('/etc/nginx/nginx.conf', '')
        self.write('/etc/apache2/apache2.conf', 'IncludeOptional /etc/apache2/sites-enabled/*.conf')
        for value, expected in (('On', 1), ('OFF', 0)):
            with self.subTest(value=value):
                self.write('/etc/apache2/sites-enabled/site.conf', '''<VirtualHost *:8443>
ServerName example.com
SSLEngine %s
SSLCertificateFile /srv/tls/fullchain.pem
SSLCertificateKeyFile /srv/tls/private.key
</VirtualHost>''' % value)
                self.assertEqual(len(self.scan()['items']), expected)
        self.write('/etc/apache2/sites-enabled/site.conf', '''<VirtualHost *:8443>
ServerName example.com
SSLEngine dynamically
SSLCertificateFile /srv/tls/fullchain.pem
</VirtualHost>''')
        with self.assertRaises(remote_probe.ProbeError):
            self.scan()

    def certbot(self, extra=''):
        self.write('/etc/letsencrypt/live/example.com/cert.pem', self.pem)
        self.write('/usr/bin/certbot', '#!/usr/bin/python3\n# fixture')
        self.write('/usr/sbin/nginx', 'fixture nginx executable')
        self.write('/etc/letsencrypt/renewal/example.com.conf', '''version = 2.9.0
archive_dir = /etc/letsencrypt/archive/example.com
cert = /etc/letsencrypt/live/example.com/cert.pem
privkey = /srv/tls/private.key
chain = /etc/letsencrypt/live/example.com/chain.pem
fullchain = /srv/tls/fullchain.pem
[renewalparams]
account = 0123456789abcdef
authenticator = webroot
server = https://acme-v02.api.letsencrypt.org/directory
webroot_path = /srv/www,
''' + extra)

    def test_unique_certbot_existing_deployment_plan(self):
        self.certbot()
        renewal = self.scan()['items'][0]['renewal']
        self.assertTrue(renewal['supported'], renewal)
        plan = renewal['plan']
        self.assertEqual(plan['engine'], 'certbot')
        self.assertEqual(plan['order'], 'example.com')
        self.assertNotIn('--force-renewal', json.dumps(plan))
        self.assertEqual(plan['summary']['reload'], 'nginx: 配置检查后平滑重载')

    def test_certbot_custom_hook_and_ambiguous_order_rejected(self):
        self.certbot('renew_hook = curl secret.example\n')
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])
        self.certbot()
        source = (self.root / 'etc/letsencrypt/renewal/example.com.conf').read_text()
        self.write('/etc/letsencrypt/renewal/other.conf', source)
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])

    def test_private_key_deployment_must_match_selected_order(self):
        self.certbot()
        self.write('/etc/letsencrypt/renewal/example.com.conf',
                   (self.root / 'etc/letsencrypt/renewal/example.com.conf').read_text().replace(
                       'privkey = /srv/tls/private.key', 'privkey = /srv/other.key'))
        renewal = self.scan()['items'][0]['renewal']
        self.assertFalse(renewal['supported'])
        self.assertEqual(renewal['reason_code'], 'deployment_mismatch')

    def test_certbot_public_order_leaf_must_match_deployment_before_mutation(self):
        self.certbot()
        self.write('/etc/letsencrypt/live/example.com/cert.pem', public_pem(('other.example.com',)))
        renewal = self.scan()['items'][0]['renewal']
        self.assertFalse(renewal['supported'])
        self.assertEqual(renewal['reason_code'], 'deployment_mismatch')
        self.assertEqual(renewal['plan'], {})
        self.write('/etc/letsencrypt/live/example.com/cert.pem', self.pem)
        self.assertTrue(self.scan()['items'][0]['renewal']['supported'])

    def test_certbot_dns_plugin_and_credentials_are_frozen_without_reading_secret(self):
        self.certbot()
        path = self.root / 'etc/letsencrypt/renewal/example.com.conf'
        text = path.read_text().replace('authenticator = webroot', 'authenticator = dns-cloudflare')
        text = text.replace('webroot_path = /srv/www,', 'dns_cloudflare_credentials = /root/.secrets/cloudflare.ini\n')
        self.write('/etc/letsencrypt/renewal/example.com.conf', text)
        self.write('/usr/lib/python3/dist-packages/certbot_dns_cloudflare/_internal/dns_cloudflare.py', '# fixture plugin')
        self.write('/root/.secrets/cloudflare.ini', 'dns_cloudflare_api_token = SECRET_TOKEN')
        renewal = self.scan()['items'][0]['renewal']
        self.assertTrue(renewal['supported'], renewal)
        self.assertNotIn('SECRET_TOKEN', json.dumps(renewal))
        self.assertIn('credential_identity', renewal['plan'])
        self.write('/root/.secrets/cloudflare.ini', 'changed credentials')
        with self.assertRaisesRegex(remote_probe.ProbeError, 'stale_plan'):
            remote_probe.renew(renewal['plan'], str(self.root), runner=mock.Mock())

    def test_acme_dns_provider_is_fixed_and_account_cannot_override_path(self):
        self.write('/usr/sbin/nginx', 'fixture nginx executable')
        self.write('/root/.acme.sh/acme.sh', '#!/bin/sh\n# fixture')
        self.write('/root/.acme.sh/dnsapi/dns_cf.sh', '# fixture provider')
        self.write('/root/.acme.sh/account.conf', "SAVED_CF_Token='SECRET_TOKEN'\n")
        self.write('/root/.acme.sh/example.com/example.com.conf', """Le_Domain='example.com'
Le_Alt='no'
Le_Webroot='dns_cf'
Le_RealFullChainPath='/srv/tls/fullchain.pem'
Le_RealKeyPath='/srv/tls/private.key'
Le_API='https://acme-v02.api.letsencrypt.org/directory'
""")
        renewal = self.scan()['items'][0]['renewal']
        self.assertTrue(renewal['supported'], renewal)
        self.assertNotIn('SECRET_TOKEN', json.dumps(renewal))
        self.write('/root/.acme.sh/account.conf', "PATH='/tmp/untrusted'\n")
        renewal = self.scan()['items'][0]['renewal']
        self.assertFalse(renewal['supported'])
        self.assertEqual(renewal['reason_code'], 'unsupported_config')

    def test_acme_literal_existing_order_and_dynamic_account_rejection(self):
        self.write('/usr/sbin/nginx', 'fixture nginx executable')
        self.write('/root/.acme.sh/acme.sh', '#!/bin/sh\n# fixture')
        self.write('/root/.acme.sh/account.conf', "ACCOUNT_EMAIL='admin@example.com'\n")
        self.write('/root/.acme.sh/example.com/example.com.conf', """Le_Domain='example.com'
Le_Alt='no'
Le_Webroot='/srv/www'
Le_RealFullChainPath='/srv/tls/fullchain.pem'
Le_RealKeyPath='/srv/tls/private.key'
Le_ReloadCmd=''
Le_API='https://acme-v02.api.letsencrypt.org/directory'
""")
        renewal = self.scan()['items'][0]['renewal']
        self.assertTrue(renewal['supported'], renewal)
        self.assertEqual(renewal['engine'], 'acme.sh')
        self.write('/root/.acme.sh/account.conf', "ACCOUNT_EMAIL=$(touch /tmp/executed)\n")
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])
        self.assertFalse((self.root / 'tmp/executed').exists())

    def test_renewal_refuses_stale_configuration_before_command(self):
        self.certbot()
        plan = self.scan()['items'][0]['renewal']['plan']
        self.write('/etc/nginx/sites-enabled/example.conf', 'changed')
        runner = mock.Mock()
        with self.assertRaisesRegex(remote_probe.ProbeError, 'stale_plan'):
            remote_probe.renew(plan, str(self.root), runner=runner)
        runner.assert_not_called()

    def test_renewal_fixed_selected_order_then_config_check_and_reload(self):
        self.certbot()
        plan = self.scan()['items'][0]['renewal']['plan']
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            self.assertFalse(kwargs.get('shell', False))
            if len(calls) == 1:
                self.write('/srv/tls/fullchain.pem', public_pem())
            return SimpleNamespace(returncode=0)
        result = remote_probe.renew(plan, str(self.root), runner=runner)
        self.assertEqual(result['state'], 'success')
        self.assertEqual(calls[0][calls[0].index('--cert-name') + 1], 'example.com')
        self.assertNotIn('--force-renewal', calls[0])
        self.assertEqual(calls[1][1], '-t')
        self.assertIn('reload', calls[2])

    def test_real_child_command_timeout_reaps_entire_group(self):
        import subprocess
        child = mock.Mock(pid=12345)
        child.wait.side_effect = [subprocess.TimeoutExpired('safe', 1), 0]
        with mock.patch.object(remote_probe.subprocess, 'Popen', return_value=child), \
                mock.patch.object(remote_probe.os, 'killpg', create=True) as kill, \
                mock.patch.object(remote_probe.signal, 'SIGKILL', 9, create=True):
            with self.assertRaises(subprocess.TimeoutExpired):
                remote_probe.run_command(['/fixed/tool'], timeout=1, env={})
        kill.assert_called_once_with(12345, 9)
        self.assertEqual(child.wait.call_count, 2)

    def test_acme_order_name_must_match_the_config_location(self):
        self.test_acme_literal_existing_order_and_dynamic_account_rejection()
        self.write('/root/.acme.sh/account.conf', "ACCOUNT_EMAIL='admin@example.com'\n")
        path = self.root / 'root/.acme.sh/example.com/example.com.conf'
        text = path.read_text().replace("Le_Domain='example.com'", "Le_Domain='other.example.com'")
        self.write('/root/.acme.sh/example.com/example.com.conf', text)
        self.assertFalse(self.scan()['items'][0]['renewal']['supported'])

    def test_acme_known_reload_command_supports_literal_and_base64_without_shell_evaluation(self):
        import base64
        self.test_acme_literal_existing_order_and_dynamic_account_rejection()
        self.write('/root/.acme.sh/account.conf', "ACCOUNT_EMAIL='admin@example.com'\n")
        config = self.root / 'root/.acme.sh/example.com/example.com.conf'
        original = config.read_text()
        command = '/usr/sbin/nginx -t && /usr/sbin/nginx -s reload'
        encoded = '__ACME_BASE64__START_' + base64.b64encode(command.encode()).decode() + '__ACME_BASE64__END_'
        for value in (command, encoded):
            with self.subTest(value=value):
                self.write('/root/.acme.sh/example.com/example.com.conf', original.replace("Le_ReloadCmd=''", "Le_ReloadCmd='" + value + "'"))
                renewal = self.scan()['items'][0]['renewal']
                self.assertTrue(renewal['supported'], renewal)
                self.assertEqual(renewal['plan']['saved_reload']['command_hash'], remote_probe.digest(command))
                self.assertIn('既有', renewal['summary']['reload'])
        for value in ('nginx -t && nginx -s reload', 'nginx -t && nginx -s reload; curl evil', 'systemctl reload nginx', 'service nginx reload',
                      '__ACME_BASE64__START_INVALID!__ACME_BASE64__END_'):
            with self.subTest(value=value):
                self.write('/root/.acme.sh/example.com/example.com.conf', original.replace("Le_ReloadCmd=''", "Le_ReloadCmd='" + value + "'"))
                self.assertFalse(self.scan()['items'][0]['renewal']['supported'])

    def test_acme_standard_saved_user_path_and_install_timestamp_are_safe_metadata(self):
        self.test_acme_literal_existing_order_and_dynamic_account_rejection()
        self.write('/root/.acme.sh/account.conf', "ACCOUNT_EMAIL='admin@example.com'\nUSER_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/root/bin'\n")
        path = self.root / 'root/.acme.sh/example.com/example.com.conf'
        self.write('/root/.acme.sh/example.com/example.com.conf', path.read_text() +
                   "Le_InstallCertSuccessTime='1770000000'\nLe_InstallCertSuccessTimeStr='2026-02-02T00:00:00Z'\n")
        renewal = self.scan()['items'][0]['renewal']
        self.assertTrue(renewal['supported'], renewal)


class TLSTests(TestCase):
    def test_internal_peer_is_pinned_independently_from_sni(self):
        der = ssl.PEM_cert_to_DER_cert(public_pem())
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        sock, context = mock.MagicMock(), mock.MagicMock()
        context.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value = der
        with mock.patch.object(tls.socket, 'getaddrinfo', return_value=addresses) as resolver, \
                mock.patch.object(tls.socket, 'socket', return_value=sock), \
                mock.patch.object(tls.ssl, 'create_default_context', return_value=context):
            result = tls._check_tls('example.com', 443, peer_hostname='panel.example.com', max_endpoints=1, timeout=2)
        self.assertTrue(result['verified'])
        self.assertEqual(resolver.call_args.args[:2], ('panel.example.com', 443))
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'example.com')
        self.assertEqual(sock.connect.call_args.args[0], ('8.8.8.8', 443))

    def test_origin_peer_cannot_bypass_private_address_rejection(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with mock.patch.object(tls.socket, 'getaddrinfo', return_value=addresses), \
                mock.patch.object(tls.socket, 'socket') as conn:
            result = tls._check_tls('example.com', 443, peer_hostname='127.0.0.1', max_endpoints=1, timeout=2)
        self.assertFalse(result['verified'])
        conn.assert_not_called()

    def test_idna_and_invalid_targets(self):
        self.assertEqual(tls.normalize_domain('BÜCHER.example.'), 'xn--bcher-kva.example')
        for value in ('127.0.0.1', '[::1]', '_', 'a/b.example', 'a.example;touch', 'https://example.com', '*.example.com'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tls.normalize_domain(value)
        self.assertEqual(tls.normalize_domain('*.Example.com', allow_wildcard=True), '*.example.com')

    def test_mixed_dns_rejects_private_and_never_connects(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with mock.patch.object(tls.socket, 'getaddrinfo', return_value=addresses), mock.patch.object(tls.socket, 'socket') as conn:
            result = tls._check_tls('example.com', 443)
        self.assertFalse(result['verified'])
        conn.assert_not_called()

    def test_certificate_validation_failure_keeps_public_expired_metadata(self):
        der = ssl.PEM_cert_to_DER_cert(public_pem(expired=True))
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        sock = mock.MagicMock()
        verified_context = mock.Mock()
        verified_context.wrap_socket.side_effect = ssl.SSLCertVerificationError(1, 'expired SECRET')
        fallback_context = mock.MagicMock()
        fallback_context.wrap_socket.return_value.__enter__.return_value.getpeercert.return_value = der
        with mock.patch.object(tls.socket, 'getaddrinfo', return_value=addresses) as resolver, \
                mock.patch.object(tls.socket, 'socket', return_value=sock), \
                mock.patch.object(tls.ssl, 'create_default_context', return_value=verified_context), \
                mock.patch.object(tls.ssl, '_create_unverified_context', return_value=fallback_context):
            result = tls._check_tls('example.com', 443)
        self.assertFalse(result['verified'])
        self.assertTrue(result['certificate']['fingerprint'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(sock.connect.call_args[0][0], ('8.8.8.8', 443))


class CollectorTests(TestCase):
    def panel_include_fixture(self, pem):
        from apps.baota.test_ssl92 import nginx_config
        client = mock.MagicMock()
        client.__enter__.return_value = client
        def request(path, action, params=None, **kwargs):
            if action == 'getData':
                if params['table'] == 'sites':
                    return {'data': [{'id': 1, 'name': 'example.com', 'php_version': '74'}]}
                return {'data': [{'id': 1, 'pid': 1, 'name': 'example.com', 'port': 80}]}
            if action == 'GetConcifInfo':
                return {'webserver': 'nginx'}
            if action == 'GetSSL':
                return {'status': True, 'csr': pem, 'key': 'NEVER_EXPORT_KEY'}
            raise AssertionError(action)
        client.request.side_effect = request
        client.read_site_web_config.return_value = nginx_config()
        client.read_site_public_certificate.return_value = pem
        return client

    def test_default_baota_includes_use_origin_leaf_for_monitoring_without_granting_renewal(self):
        pem = public_pem(expired=True)
        client = self.panel_include_fixture(pem)
        public = collectors.public_certificate(pem)
        origin = {'verified': False, 'certificate': public, 'message': '公网证书已过期'}
        with mock.patch.object(collectors, 'PanelClient', return_value=client), \
                mock.patch.object(tls, '_check_tls', return_value=origin) as check:
            result = collectors._discover_panel(SimpleNamespace(api_base_url='https://panel.example:8888'))
        row = result['items'][0]
        self.assertTrue(row['deployment_verified'])
        self.assertEqual(row['certificate'], public)
        self.assertFalse(row['renewal']['supported'])
        self.assertEqual(row['renewal']['reason_code'], 'unverified_includes')
        self.assertEqual(row['deployment_message'], '源站 TLS 证书与面板副本一致；未遍历引用')
        self.assertEqual(check.call_args.args[:2], ('example.com', 443))
        self.assertEqual(check.call_args.kwargs['peer_hostname'], 'panel.example')
        self.assertNotIn('NEVER_EXPORT_KEY', json.dumps(result))
        self.assertFalse(any(call.args[1] in ('GetDir', 'GetDirNew', 'GetFileBody') for call in client.request.call_args_list))

    def test_baota_origin_different_leaf_keeps_canonical_fact_unverified(self):
        pem = public_pem()
        client = self.panel_include_fixture(pem)
        origin = {'verified': True, 'certificate': collectors.public_certificate(public_pem()), 'message': ''}
        with mock.patch.object(collectors, 'PanelClient', return_value=client), \
                mock.patch.object(tls, '_check_tls', return_value=origin):
            row = collectors._discover_panel(SimpleNamespace(api_base_url='https://panel.example:8888'))['items'][0]
        self.assertTrue(row['certificate'])
        self.assertFalse(row['deployment_verified'])
        self.assertFalse(row['renewal']['supported'])

    def test_normalized_plan_freezes_public_sans_and_remote_accepts_derived_baseline(self):
        fixture = ProbeTests()
        fixture.setUp()
        try:
            fixture.certbot()
            snapshot = fixture.scan()
            normalized = collectors._normalize_host(snapshot)
            plan = normalized['items'][0]['renewal']['plan']
            self.assertEqual(plan['public_baseline']['domains'], ['example.com'])
            self.assertTrue(remote_probe.matching_plan(fixture.scan(), plan))
        finally:
            fixture.doCleanups()

    def test_tool_success_rejects_different_san_set_or_unchanged_dates(self):
        before = collectors.public_certificate(public_pem())
        raw = {'state': 'success', 'changed': True, 'message': 'safe',
               'certificates': [{'cert_path': '/cert.pem', 'public_pem': public_pem(('other.example.com',))}]}
        plan = {'public_baseline': before, 'cert_path': '/cert.pem'}
        with self.assertRaises(collectors.CollectorError):
            collectors._host_result(raw, plan)

    def test_real_disposable_worker_bootstraps_without_network(self):
        result = collectors.run_worker({'action': 'tls', 'domain': '127.0.0.1', 'port': 443})
        self.assertFalse(result['verified'])

    def test_public_certificate_whitelist_and_expired_dates(self):
        public = collectors.public_certificate(public_pem(expired=True))
        self.assertEqual(set(public), {'fingerprint', 'domains', 'not_before', 'expires_at'})
        self.assertTrue(public['expires_at'].endswith('+00:00'))

    def test_panel_getssl_never_exports_private_key(self):
        pem = public_pem()
        client = mock.MagicMock()
        client.__enter__.return_value = client
        def request(path, action, params=None, **kwargs):
            if action == 'getData':
                if params['table'] == 'sites':
                    return {'data': [{'id': 1, 'name': 'example.com'}]}
                return {'data': [{'id': 1, 'pid': 1, 'name': 'example.com', 'port': 80}]}
            if action == 'GetSSL':
                return {'status': True, 'csr': pem, 'key': 'PRIVATE_KEY_SENTINEL'}
            if action == 'GetConcifInfo':
                return {'webserver': 'nginx'}
            raise AssertionError(action)
        client.request.side_effect = request
        client.read_site_public_certificate.return_value = pem
        client.read_site_web_config.return_value = '''server {
listen 8443 ssl; server_name example.com;
ssl_certificate /www/server/panel/vhost/cert/example.com/fullchain.pem;
ssl_certificate_key /www/server/panel/vhost/cert/example.com/privkey.pem;
}'''
        with mock.patch.object(collectors, 'PanelClient', return_value=client):
            result = collectors._discover_panel(object())
        self.assertEqual(result['items'][0]['certificate']['domains'], ['example.com'])
        self.assertEqual(result['items'][0]['port'], 8443)
        self.assertNotIn('PRIVATE_KEY_SENTINEL', json.dumps(result))
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(result))

    def test_panel_canonical_getssl_cannot_claim_custom_deployment_certificate(self):
        client = mock.MagicMock()
        client.__enter__.return_value = client
        pem = public_pem()
        def request(path, action, params=None, **kwargs):
            if action == 'getData':
                return {'data': [{'id': 1, 'name': 'example.com'}] if params['table'] == 'sites' else []}
            if action == 'GetConcifInfo':
                return {'webserver': 'nginx'}
            return {'status': True, 'csr': pem, 'key': 'SECRET'}
        client.request.side_effect = request
        client.read_site_web_config.return_value = '''server {
listen 443 ssl; server_name example.com;
ssl_certificate /tmp/custom/fullchain.pem;
ssl_certificate_key /tmp/custom/private.key;
}'''
        with mock.patch.object(collectors, 'PanelClient', return_value=client):
            result = collectors._discover_panel(object())
        self.assertTrue(result['items'][0]['certificate'])
        self.assertFalse(result['items'][0]['deployment_verified'])
        self.assertFalse(result['items'][0]['renewal']['supported'])

    def test_specific_untrusted_certificate_error_is_safe(self):
        error = ssl.SSLCertVerificationError(1, 'SECRET')
        error.verify_code = 10
        with mock.patch.object(tls, '_public_addresses', return_value=[object()]), \
                mock.patch.object(tls, '_handshake', side_effect=error):
            result = tls._check_tls('example.com', 443)
        self.assertEqual(result['message'], '公网证书已过期')

    def test_old_paramiko_connect_uses_supported_timeout_keys(self):
        channel = mock.MagicMock()
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        ssh = mock.MagicMock(arguments={})
        ssh.get_client.return_value.get_transport.return_value.open_session.return_value = channel
        with mock.patch.object(collectors, '_read_output', return_value={'items': [], 'warnings': []}):
            collectors._host_transport(SimpleNamespace(get_ssh=lambda: ssh), 'discover')
        self.assertNotIn('channel_timeout', ssh.arguments)
        self.assertTrue(channel.exec_command.call_args[0][0].startswith('python3 -c '))

    def test_worker_timeout_kills_and_reaps(self):
        import subprocess
        child = mock.Mock()
        child.communicate.side_effect = subprocess.TimeoutExpired('safe', 30)
        with mock.patch.object(collectors.subprocess, 'Popen', return_value=child):
            with self.assertRaises(collectors.CollectorError):
                collectors.run_worker({'action': 'discover', 'kind': 'host'})
        child.kill.assert_called_once()
        child.wait.assert_called_once()

    def test_unverified_host_never_creates_worker(self):
        with mock.patch.object(collectors, 'run_worker') as worker:
            with self.assertRaises(collectors.CollectorError):
                collectors.discover('host', SimpleNamespace(is_verified=False))
        worker.assert_not_called()
