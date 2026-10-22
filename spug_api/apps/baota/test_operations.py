"""Adapter contracts against a stateful, credential-free panel protocol fixture."""
import copy
import datetime
import importlib
import json
from unittest import TestCase

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .client import PanelAPIError


def adapters():
    try:
        return importlib.import_module('apps.baota.operations')
    except ImportError:
        return None


class PanelFixture:
    def __init__(self):
        self.calls = []
        self.info = {'version': '13.1.0', 'system': 'Debian GNU/Linux 13', 'cpuNum': 2, 'memTotal': 2048}
        self.webserver = 'nginx'
        self.sites = [{'id': 1, 'name': 'example.com', 'path': '/www/wwwroot/example.com',
                       'project_type': 'PHP', 'php_version': '7.4', 'status': '1'}]
        self.domains = [{'id': 1, 'pid': 1, 'name': 'example.com', 'port': 80}]
        self.basedir = {1: False}
        self.rules = {'/www/server/panel/vhost/rewrite/example.com.conf': ''}
        self.tasks = []
        self.software = {'nginx': {'name': 'nginx', 'title': 'Nginx', 'setup': False, 'type': 5, 'price': 0,
                         'version': '', 'versions': [{'m_version': '1.24', 'version': '0',
                             'cpu_limit': 0, 'mem_limit': 0, 'os_limit': 0, 'dependnet': ''}],
                         'dependnet': [], 'mutex': '', 'task': '1'}}
        self.databases = []
        self.libs = [{'name': 'redis', 'check': 'redis.so', 'phpversions': ['74'],
                      'status': False, 'task': '1'}]
        self.modules = []
        self.orders = []
        self.ssl = {'status': False, 'csr': '', 'key': 'PRIVATE_SECRET', 'index': ''}
        self.ssl_by_name = {}
        self.file_bodies = {}
        self.mutation_error = None
        self.reject_rewrite = False
        self.offered = '13.1.0'

    def request(self, path, action=None, params=None, mutating=False, **kwargs):
        p = copy.deepcopy(params or {})
        self.calls.append((path, action, p, mutating, kwargs))
        if mutating and self.mutation_error:
            raise self.mutation_error
        if action == 'GetSystemTotal': return copy.deepcopy(self.info)
        if action == 'GetConcifInfo': return {'webserver': self.webserver, 'mysql_root': 'SECRET'}
        if action == 'getData':
            rows = {'sites': self.sites, 'domain': self.domains, 'tasks': self.tasks, 'databases': self.databases}[p['table']]
            start = (int(p.get('p', 1)) - 1) * int(p.get('limit', 100))
            return {'data': copy.deepcopy(rows[start:start + int(p.get('limit', 100))]), 'page': ''}
        if action == 'GetPHPVersion': return [{'version': '74'}, {'version': '00'}]
        if action == 'GetDirUserINI': return {'userini': self.basedir[p['id']], 'runPath': {'runPath': '/'}}
        if action == 'GetSiteRunPath': return {'runPath': '/'}
        if action == 'multiple_basedir':
            for sid in json.loads(p['site_ids']): self.basedir[sid] = p['stat'] == 'open'
            return {'succeed': [{'id': sid}], 'errors': []}
        if action == 'GetRewriteLists':
            return {'site_rewrites': [{'id': s['id'], 'name': s['name'],
                    'file': '/www/server/panel/vhost/rewrite/' + s['name'] + '.conf'} for s in self.sites
                    if s['id'] in json.loads(p['site_ids'])], 'template_rewrites': [
                    {'name': 'wordpress', 'file': '/www/server/panel/rewrite/nginx/wordpress.conf'}], 'err_site': []}
        if action == 'GetFileBody':
            return {'status': True, 'data': {**self.rules, **self.file_bodies}[p['path']]}
        if action == 'SetRewriteLists':
            s = json.loads(p['sites'])[0]
            if not self.reject_rewrite: self.rules[s['file']] = p['rewrite_data']
            return [{'id': s['id'], 'status': not self.reject_rewrite, 'msg': 'REMOTE_SECRET'}]
        if action == 'AddSite':
            domain = json.loads(p['webname'])
            sid = max(s['id'] for s in self.sites) + 1
            self.sites.append({'id': sid, 'name': domain['domain'], 'path': p['path'],
                               'project_type': 'PHP', 'php_version': p['version'], 'status': '1'})
            for name in [domain['domain']] + domain['domainlist']:
                parts = name.split(':')
                self.domains.append({'id': len(self.domains) + 1, 'pid': sid, 'name': name, 'port': int(p['port'])})
                self.domains[-1].update(name=parts[0], port=int(parts[1]) if len(parts) == 2 else int(p['port']))
            return {'siteStatus': True}
        if action == 'get_soft_list': return {'list': {'data': list(self.software.values()), 'page': ''}, 'skey': 'SECRET'}
        if action == 'get_soft_find': return copy.deepcopy(self.software[p['sName']])
        if action in ('install_plugin', 'InstallSoft'):
            name = p.get('sName', p.get('name'))
            name = name.split('-')[0] if name.startswith('php-') else name
            version = p['version'] + ('.' + p['min_version'] if 'min_version' in p else '')
            self.tasks.append({'id': len(self.tasks) + 1, 'name': '安装[' + name + '-' + version + ']', 'status': '0'})
            return {'status': True, 'msg_id': 999, 'private_key': 'SECRET'}
        if action == 'uninstall_plugin':
            self.software[p['sName']]['setup'] = False
            self.software[p['sName']]['version'] = ''
            return {'status': True}
        if action == 'GetPHPConfig': return {'libs': copy.deepcopy(self.libs), 'ini': 'SECRET'}
        if action == 'php_info': return {'modules': self.modules, 'phpinfo': {'modules': ' '.join(self.modules)}, 'ini': 'SECRET'}
        if action == 'upgrade_panel':
            if 'force' in p: return {'status': True, 'msg': 'started'}
            return {'local': {'version': self.info['version']}, 'cloud': {
                'OfficialVersion': {'version': self.offered}, 'OfficialVersionLatest': []}, 'upgrade': 1}
        if action == 'GetSSL':
            ssl_state = self.ssl_by_name.get(p['siteName'], self.ssl)
            if not kwargs.get('allow_status_false') and ssl_state['status'] is False:
                raise PanelAPIError('disabled')
            return copy.deepcopy(ssl_state)
        if action == 'get_orders': return copy.deepcopy(self.orders)
        if action in ('apply_cert_api', 'renew_cert'): return {'status': True, 'index': 'a' * 32}
        if action == 'SetCertToSite': return {'status': True}
        raise AssertionError('Unexpected protocol: ' + str((path, action, p)))


def pem(domains=('example.com',), days=90):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Fixture CA')]))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=365)).not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]), False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class OperationTests(TestCase):
    def setUp(self):
        self.api = adapters()
        if self.api is None and self._testMethodName != 'test_registry_has_nine_codes_eight_categories':
            self.skipTest('Adapters not implemented yet')
        self.client = PanelFixture()

    def op(self, code): return self.api.get_operation(code)
    def item(self, code, params):
        op = self.op(code)
        return op, op.preflight(self.client, op.validate(params))['items'][0]

    def mutations(self): return [c for c in self.client.calls if c[3]]

    def test_registry_has_nine_codes_eight_categories(self):
        self.assertIsNotNone(self.api, 'Controlled operation adapters must exist')
        self.assertEqual(len(self.api.OPERATIONS), 9)
        self.assertEqual(len({v.category for v in self.api.OPERATIONS.values()}), 8)
        with self.assertRaises(ValueError): self.api.get_operation('shell')

    def test_validation_rejects_unknown_fields_and_shell_tokens(self):
        for code, params in [('site_basedir', {'sites': [1], 'enabled': True, 'file': '/etc/passwd'}),
                             ('software_install', {'name': 'nginx;touch /tmp/x', 'm_version': '1.24', 'min_version': '0'}),
                             ('php_extension_install', {'php_version': '74;id', 'extensions': ['redis']}),
                             ('site_add', {'entries': [{'domains': ['example.com;id'], 'path': '/www/wwwroot/x', 'php_version': '00'}]}),
                             ('ssl_apply', {'sites': [True]}),
                             ('site_add', {'entries': [{'domains': ['x.example.com'], 'path': '/etc/x', 'php_version': '00'}]})]:
            with self.subTest(code=code), self.assertRaises(ValueError): self.op(code).validate(params)

    def test_unknown_versions_and_non_linux_are_refused(self):
        for info in [{'version': '13.1.1', 'system': 'Debian 13'}, {'version': '13.1.0', 'system': 'Windows'}]:
            self.client.info = info
            with self.assertRaises((ValueError, PanelAPIError)):
                self.item('site_basedir', {'sites': [1], 'enabled': True})
        self.assertEqual(self.mutations(), [])

    def test_all_sites_are_frozen_and_id_reuse_is_refused(self):
        op, item = self.item('site_basedir', {'all_sites': True, 'enabled': True})
        self.client.sites[0]['name'] = 'replacement.example.com'
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(item['target']['id'], 1)
        self.assertEqual(self.mutations(), [])

    def test_basedir_verifies_readback_and_already_is_noop(self):
        op, item = self.item('site_basedir', {'sites': [1], 'enabled': True})
        self.assertEqual(op.execute(self.client, item)['state'], 'success')
        op, already = self.item('site_basedir', {'sites': [1], 'enabled': True})
        count = len(self.mutations())
        self.assertEqual(already['state'], 'already')
        self.assertEqual(op.execute(self.client, already)['state'], 'already')
        self.assertEqual(len(self.mutations()), count)

    def test_live_version_change_blocks_mutation(self):
        op, item = self.item('site_basedir', {'sites': [1], 'enabled': True})
        self.client.info['version'] = '99.0.0'
        self.assertEqual(op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_non_php_child_is_skipped_without_hiding_other_children(self):
        self.client.sites.append({'id': 2, 'name': 'node.example.com', 'path': '/www/wwwroot/node', 'project_type': 'Node', 'status': '1'})
        result = self.op('site_basedir').preflight(self.client, {'all_sites': True, 'enabled': True})
        self.assertEqual([x['state'] for x in result['items']], ['ready', 'skipped'])

    def test_rewrite_sends_full_text_and_preserves_changed_configuration(self):
        params = {'sites': [1], 'mode': 'custom', 'webserver': 'nginx', 'rules': 'location / { try_files $uri $uri/ /index.php; }\n'}
        op, item = self.item('site_rewrite', params)
        self.client.rules[item['plan']['file']] = '# changed by operator\n'
        self.assertEqual(op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])
        op, item = self.item('site_rewrite', params)
        self.assertEqual(op.execute(self.client, item)['state'], 'uncertain')
        self.assertEqual(self.mutations()[0][2]['rewrite_data'], params['rules'])

    def test_rejected_rewrite_checks_rollback_without_second_mutation(self):
        op, item = self.item('site_rewrite', {'sites': [1], 'mode': 'custom', 'webserver': 'nginx', 'rules': 'invalid;\n'})
        self.client.reject_rewrite = True
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'failed')
        self.assertTrue(result['evidence']['rollback_verified'])
        self.assertEqual(len(self.mutations()), 1)
        self.assertNotIn('REMOTE_SECRET', json.dumps(result))

    def test_rejected_rewrite_remains_failed_when_rollback_left_desired_text(self):
        op, item = self.item('site_rewrite', {'sites': [1], 'mode': 'custom', 'webserver': 'nginx', 'rules': 'invalid;\n'})
        original = self.client.request
        def request(path, action=None, params=None, mutating=False, **kwargs):
            if action == 'SetRewriteLists':
                original(path, action, params, mutating, **kwargs)
                return [{'id': 1, 'status': False, 'msg': 'invalid directive and backup restore failed'}]
            return original(path, action, params, mutating, **kwargs)
        self.client.request = request
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['evidence']['rollback_verified'])
        self.assertEqual(len(self.mutations()), 1)

    def test_addsite_has_no_implicit_ftp_database_and_verifies_domains(self):
        op, item = self.item('site_add', {'entries': [{'domains': ['new.example.com', 'www.new.example.com'],
            'path': '/www/wwwroot/new.example.com', 'php_version': '00'}]})
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'success')
        p = self.mutations()[0][2]
        self.assertEqual((p['ftp'], p['sql'], p['type']), ('false', 'false', 'PHP'))

    def test_upgrade_preflight_never_passes_force_or_legacy_action(self):
        self.client.info['version'] = '11.7.0'
        op, item = self.item('panel_upgrade', {'version': '13.1.0'})
        reads = [c for c in self.client.calls if c[1] == 'upgrade_panel']
        self.assertTrue(reads)
        self.assertTrue(all('force' not in c[2] for c in reads))
        self.assertEqual(op.execute(self.client, item)['state'], 'remote')
        self.assertEqual(self.mutations()[0][2], {'force': 'true', 'version': '13.1.0'})
        self.client.info['version'] = '13.1.0'
        self.assertEqual(op.reconcile(self.client, item, {})['state'], 'success')
        self.assertFalse(any(c[1] in ('UpdatePanel', 'upgrade_env') for c in self.client.calls))

    def test_software_terminal_queue_is_not_installed_version_proof(self):
        op, item = self.item('software_install', {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})
        dispatched = op.execute(self.client, item)
        self.assertEqual(dispatched['state'], 'remote')
        self.assertEqual(dispatched['remote']['task_ids'], [1])
        self.assertNotIn(999, dispatched['remote']['task_ids'])
        self.client.tasks[0]['status'] = '1'
        self.client.software['nginx'].update(setup=True, version='1.23.0')
        self.assertEqual(op.reconcile(self.client, item, dispatched['remote'])['state'], 'failed')
        self.client.software['nginx']['version'] = '1.24.0'
        self.assertEqual(op.reconcile(self.client, item, dispatched['remote'])['state'], 'success')

    def test_catalog_membership_and_existing_version_protect_install(self):
        op, item = self.item('software_install', {'name': 'nginx', 'm_version': '9.9', 'min_version': '0'})
        self.assertEqual(item['state'], 'failed')
        self.client.software['nginx'].update(setup=True, version='1.22.0')
        op, item = self.item('software_install', {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_queue_collision_does_not_claim_other_operation(self):
        self.client.tasks = [{'id': 3, 'name': '安装[nginx-1.24.0]', 'status': '0'}]
        op, item = self.item('software_install', {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_php_extension_needs_actual_loaded_module(self):
        op, item = self.item('php_extension_install', {'php_version': '74', 'extensions': ['redis']})
        result = op.execute(self.client, item)
        self.client.tasks[0]['status'] = '1'
        self.client.libs[0]['status'] = True
        self.assertEqual(op.reconcile(self.client, item, result['remote'])['state'], 'failed')
        self.client.modules = ['redis']
        self.assertEqual(op.reconcile(self.client, item, result['remote'])['state'], 'success')

    def test_software_uninstall_uses_store_endpoint(self):
        self.client.software['nginx'].update(setup=True, version='1.24.0')
        self.client.sites = []
        op, item = self.item('software_uninstall', {'name': 'nginx'})
        self.assertEqual(op.execute(self.client, item)['state'], 'success')
        self.assertEqual(self.mutations()[0][:2], ('/plugin', 'uninstall_plugin'))
        self.assertEqual(self.mutations()[0][2]['version'], '1.24')

    def test_ssl_pending_never_succeeds_and_reconcile_never_mutates(self):
        op, item = self.item('ssl_apply', {'sites': [1]})
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'remote')
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'pending', 'private_key': 'SECRET'}]
        count = len(self.mutations())
        result = op.reconcile(self.client, item, result['remote'])
        self.assertEqual(result['state'], 'remote')
        self.assertEqual(len(self.mutations()), count)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_ssl_issue_and_deploy_are_distinct_durable_phases(self):
        op, item = self.item('ssl_apply', {'sites': [1]})
        result = op.execute(self.client, item)
        certificate = pem()
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = certificate
        result = op.reconcile(self.client, item, result['remote'])
        self.assertEqual(result['remote']['next_action'], 'deploy')
        self.assertEqual(len(self.mutations()), 1)
        result = op.dispatch(self.client, item, 'deploy', result['remote'])
        self.assertNotEqual(result['state'], 'success')
        self.assertEqual(len(self.mutations()), 2)
        self.client.ssl.update(status=True, csr=certificate, index='a' * 32)
        result = op.reconcile(self.client, item, result['remote'])
        self.assertEqual(result['state'], 'success')
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(result))
        self.assertNotIn('PRIVATE_SECRET', json.dumps(result))

    def test_ssl_renew_requires_linked_order_and_rejects_old_certificate(self):
        old = pem(days=5)
        self.client.ssl.update(status=True, csr=old, index='a' * 32, auth_type='http', type=1)
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = old
        op, item = self.item('ssl_renew', {'sites': [1]})
        result = op.execute(self.client, item)
        self.assertNotEqual(result['state'], 'success')
        self.assertNotEqual(op.reconcile(self.client, item, result['remote'])['state'], 'success')
        self.assertEqual(self.mutations()[0][2], {'index': 'a' * 32})

    def test_uncertain_mutation_is_never_automatically_reissued(self):
        op, item = self.item('site_rewrite', {'sites': [1], 'mode': 'custom', 'webserver': 'nginx', 'rules': '# desired\n'})
        self.client.mutation_error = PanelAPIError('timeout', uncertain=True)
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'uncertain')
        count = len(self.mutations())
        self.client.mutation_error = None
        op.reconcile(self.client, item, result['remote'])
        self.assertEqual(len(self.mutations()), count)

    def test_resources_drop_unknown_and_secret_fields(self):
        resources = importlib.import_module('apps.baota.resources')
        result = resources.get_resources(self.client)
        self.assertIn('sites', result)
        self.assertIn('software', result)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_expired_certificate_can_be_renewed(self):
        self.client.ssl.update(status=True, csr=pem(days=-1), index='a' * 32, auth_type='http', type=1)
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        _, item = self.item('ssl_renew', {'sites': [1]})
        self.assertEqual(item['state'], 'ready')

    def test_readback_failure_after_accepted_mutation_remains_remote(self):
        op, item = self.item('site_basedir', {'sites': [1], 'enabled': True})
        original = self.client.request
        accepted = False
        def request(path, action=None, params=None, mutating=False, **kwargs):
            nonlocal accepted
            if accepted and not mutating:
                raise PanelAPIError('readback unavailable')
            response = original(path, action, params, mutating, **kwargs)
            accepted = accepted or mutating
            return response
        self.client.request = request
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'remote')
        self.assertEqual(result['remote']['phase'], 'apply')

    def test_uncertain_deploy_preserves_order_but_never_requests_automatic_redeployment(self):
        op, item = self.item('ssl_apply', {'sites': [1]})
        result = op.execute(self.client, item)
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = pem()
        result = op.reconcile(self.client, item, result['remote'])
        self.client.mutation_error = PanelAPIError('timeout', uncertain=True)
        result = op.dispatch(self.client, item, 'deploy', result['remote'])
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['remote']['index'], 'a' * 32)
        self.assertNotIn('next_action', result['remote'])

    def test_resources_include_safe_site_specific_options(self):
        resources = importlib.import_module('apps.baota.resources')
        result = resources.get_resources(self.client, site_id=1)
        self.assertEqual(result['site_options']['domains'], [{'name': 'example.com', 'port': 80}])
        self.assertEqual(result['site_options']['rewrite_templates'], ['wordpress'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_software_preflight_displays_and_enforces_requirements(self):
        self.client.software['nginx']['versions'][0]['cpu_limit'] = 4
        _, item = self.item('software_install', {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(item['plan']['requirements']['cpu_cores'], 4)
        self.assertIn('cpu', item['plan']['blockers'])
        self.client.software['nginx']['versions'][0]['cpu_limit'] = 0
        self.client.software['nginx']['dependnet'] = {'need': ['redis'], 'selected': []}
        _, item = self.item('software_install', {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(item['plan']['requirements']['missing_dependencies'], [['redis']])
        self.client.software['nginx']['dependnet'] = ''
        self.client.software['nginx']['price'] = 10
        _, item = self.item('software_install', {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})
        self.assertEqual(item['state'], 'failed')
        self.assertIn('license', item['plan']['blockers'])

    def test_uninstall_mysql_database_impact_is_safe_and_blocks_mutation(self):
        self.client.software['mysql'] = {**copy.deepcopy(self.client.software['nginx']),
            'name': 'mysql', 'setup': True, 'version': '8.0.40',
            'versions': [{'m_version': '8.0', 'version': '40'}]}
        self.client.databases = [{'id': 8, 'name': 'customer_db', 'type': 'MySQL', 'db_type': 0,
                                  'password': 'DATABASE_SECRET'}]
        op, item = self.item('software_uninstall', {'name': 'mysql'})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(item['plan']['impact']['local_databases'], 1)
        self.assertNotIn('DATABASE_SECRET', json.dumps(item))
        op.execute(self.client, item)
        self.assertEqual(self.mutations(), [])

    def test_uninstall_php_exposes_target_branch_website_references(self):
        self.client.software['php-7.4'] = {**copy.deepcopy(self.client.software['nginx']),
            'name': 'php-7.4', 'setup': True, 'version': '7.4.34',
            'versions': [{'m_version': '7.4', 'version': '34'}]}
        _, item = self.item('software_uninstall', {'name': 'php-7.4'})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(item['plan']['impact']['website_refs'], [{'id': 1, 'name': 'example.com'}])

    def test_secondary_domains_explicitly_keep_non_default_port(self):
        op, item = self.item('site_add', {'entries': [{'domains': ['new.example.com', 'www.new.example.com'],
            'path': '/www/wwwroot/new.example.com', 'php_version': '00', 'port': 8080}]})
        self.assertEqual(op.execute(self.client, item)['state'], 'success')
        webname = json.loads(self.mutations()[0][2]['webname'])
        self.assertEqual(webname['domainlist'], ['www.new.example.com:8080'])

    def test_selective_resource_reader_avoids_unrelated_catalog_requests(self):
        resources = importlib.import_module('apps.baota.resources')
        result = resources.get_resource(self.client, 'sites')
        self.assertEqual(result['sites'][0]['id'], 1)
        self.assertFalse(any(c[1] == 'get_soft_list' for c in self.client.calls))
        result = resources.get_resource(self.client, 'upgrade_versions')
        self.assertEqual(result['versions'], [])
        with self.assertRaises(ValueError): resources.get_resource(self.client, 'shell')
        with self.assertRaises(ValueError): resources.get_resource(self.client, 'sites', site_id=1)

    def test_expired_saved_order_does_not_block_new_application(self):
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = pem(days=-1)
        _, item = self.item('ssl_apply', {'sites': [1]})
        self.assertEqual(item['state'], 'ready')
        self.assertNotIn('reuse_index', item['plan'])

    def test_near_expiry_old_order_is_not_claimed_as_new_application(self):
        old = pem(days=5)
        self.client.ssl.update(status=True, csr=old, index='a' * 32, auth_type='http', type=1)
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = old
        op, item = self.item('ssl_apply', {'sites': [1]})
        self.assertEqual(item['state'], 'ready')
        self.assertNotIn('reuse_index', item['plan'])
        self.assertNotEqual(op.execute(self.client, item)['state'], 'success')

    def test_apache_nested_run_path_rejects_official_inactive_file(self):
        self.client.webserver = 'apache'
        path = '/www/wwwroot/example.com/public.htaccess'
        self.client.rules[path] = ''
        original = self.client.request
        def request(route, action=None, params=None, mutating=False, **kwargs):
            if action == 'GetSiteRunPath': return {'runPath': '/public'}
            if action == 'GetRewriteLists':
                return {'site_rewrites': [{'id': 1, 'name': 'example.com', 'file': path}], 'template_rewrites': []}
            return original(route, action, params, mutating, **kwargs)
        self.client.request = request
        op, item = self.item('site_rewrite', {'sites': [1], 'webserver': 'apache', 'mode': 'custom', 'rules': 'RewriteEngine On\n'})
        self.assertEqual(item['state'], 'failed')
        self.assertNotEqual(op.execute(self.client, item)['state'], 'success')
        self.assertEqual(self.mutations(), [])

    def test_apache_nested_rewrite_does_not_block_ssl_resources_or_create_inactive_file(self):
        self.client.webserver = 'apache'
        original = self.client.request
        def request(route, action=None, params=None, mutating=False, **kwargs):
            if action == 'GetSiteRunPath': return {'runPath': '/public'}
            if action == 'GetRewriteLists':
                self.fail('Nested Apache resolver must never be called: it creates an inactive file')
            return original(route, action, params, mutating, **kwargs)
        self.client.request = request
        resources = importlib.import_module('apps.baota.resources')
        options = resources.get_resource(self.client, 'site_options', site_id=1)['site_options']
        self.assertEqual(options['domains'], [{'name': 'example.com', 'port': 80}])
        self.assertEqual(options['ssl']['enabled'], False)
        self.assertEqual(options['rewrite_templates'], [])
        self.assertFalse(options['rewrite_available'])

    def test_apache_run_path_drift_blocks_previously_ready_dispatch(self):
        self.client.webserver = 'apache'
        self.client.rules['/www/wwwroot/example.com/.htaccess'] = ''
        original = self.client.request
        run = '/'
        def request(route, action=None, params=None, mutating=False, **kwargs):
            if action == 'GetSiteRunPath': return {'runPath': run}
            if action == 'GetRewriteLists':
                return {'site_rewrites': [{'id': 1, 'name': 'example.com', 'file': '/www/wwwroot/example.com/.htaccess'}],
                        'template_rewrites': []}
            return original(route, action, params, mutating, **kwargs)
        self.client.request = request
        op, item = self.item('site_rewrite', {'sites': [1], 'webserver': 'apache', 'mode': 'custom', 'rules': 'RewriteEngine On\n'})
        self.assertEqual(item['state'], 'ready')
        run = '/public'
        self.assertEqual(op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])
        self.client.rules['/www/wwwroot/example.com/.htaccess'] = item['plan']['rules']
        self.assertNotEqual(op.reconcile(self.client, item, {'phase': 'apply'})['state'], 'success')

    def test_malformed_resource_fields_cannot_be_stringified_into_preview(self):
        resources = importlib.import_module('apps.baota.resources')
        self.client.sites[0]['project_type'] = {'api_key': 'SECRET'}
        with self.assertRaises((ValueError, PanelAPIError)): resources.get_resource(self.client, 'sites')

    def test_uninstall_target_remains_identity_only(self):
        _, item = self.item('software_uninstall', {'name': 'nginx'})
        self.assertEqual(item['target'], {'name': 'nginx'})

    def test_php_uninstall_uses_branch_when_installed_patch_is_no_longer_offered(self):
        self.client.sites = []
        self.client.software['php-7.4'] = {**copy.deepcopy(self.client.software['nginx']),
            'name': 'php-7.4', 'setup': True, 'version': '7.4.33',
            'versions': [{'m_version': '7.4', 'version': '34'}]}
        op, item = self.item('software_uninstall', {'name': 'php-7.4'})
        self.assertEqual(op.execute(self.client, item)['state'], 'success')
        self.assertEqual(self.mutations()[0][2]['version'], '7.4')

    def test_remote_paths_with_shell_metacharacters_are_rejected(self):
        self.client.sites[0]['path'] = '/www/wwwroot/example(\'id\')'
        resources = importlib.import_module('apps.baota.resources')
        with self.assertRaises(PanelAPIError): resources.get_resource(self.client, 'sites')

    def test_ssl_failed_deployment_never_reports_success(self):
        op, item = self.item('ssl_apply', {'sites': [1]})
        result = op.execute(self.client, item)
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = pem()
        result = op.reconcile(self.client, item, result['remote'])
        self.client.mutation_error = PanelAPIError('refused', uncertain=True)
        result = op.dispatch(self.client, item, 'deploy', result['remote'])
        self.client.mutation_error = None
        result = op.reconcile(self.client, item, result['remote'])
        self.assertNotEqual(result['state'], 'success')
        self.assertNotIn('next_action', result['remote'])

    def test_extension_children_dispatch_individually_and_ols_is_skipped(self):
        self.client.libs.append({'name': 'imagick', 'check': 'imagick.so', 'phpversions': ['74'], 'status': False, 'task': '1'})
        op = self.op('php_extension_install')
        params = op.validate({'php_version': '74', 'extensions': ['redis', 'imagick']})
        items = op.preflight(self.client, params)['items']
        self.assertEqual(len(items), 2)
        op.execute(self.client, items[0])
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.mutations()[0][2]['name'], 'redis')
        self.client.webserver = 'openlitespeed'
        items = op.preflight(self.client, params)['items']
        self.assertTrue(all(i['state'] == 'skipped' for i in items))

    def test_rewrite_template_is_read_and_frozen_as_actual_text(self):
        rules = 'location / { try_files $uri /index.php?$args; }\n'
        self.client.file_bodies['/www/server/panel/rewrite/nginx/wordpress.conf'] = rules
        op, item = self.item('site_rewrite', {'sites': [1], 'mode': 'template', 'webserver': 'nginx', 'template': 'wordpress'})
        self.client.file_bodies['/www/server/panel/rewrite/nginx/wordpress.conf'] = '# newer global template\n'
        self.assertEqual(op.execute(self.client, item)['state'], 'uncertain')
        self.assertEqual(self.mutations()[0][2]['rewrite_data'], rules)

    def test_saved_rewrite_cannot_prove_reload_even_when_api_returns_true(self):
        op, item = self.item('site_rewrite', {'sites': [1], 'mode': 'custom', 'webserver': 'nginx', 'rules': '# desired\n'})
        # The real API returns child status:true before serviceReload and ignores its result.
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['message'], '配置已保存，Web服务加载结果需核实')
        self.assertTrue(result['evidence']['configuration_saved'])
        self.assertFalse(result['evidence']['runtime_verified'])
        count = len(self.mutations())
        self.assertEqual(op.reconcile(self.client, item, result['remote'])['state'], 'uncertain')
        self.assertEqual(len(self.mutations()), count)

    def ssl_sync_fixture(self):
        old = pem(days=5)
        self.client.ssl.update(status=True, csr=old, index='a' * 32, auth_type='http', type=1)
        self.client.sites.append({'id': 2, 'name': 'unselected.example.com', 'path': '/www/wwwroot/unselected',
                                  'project_type': 'PHP', 'php_version': '74', 'status': '1'})
        self.client.ssl_by_name['unselected.example.com'] = copy.deepcopy(self.client.ssl)
        self.client.file_bodies['/www/server/panel/ssl/certificate.pem'] = old
        return old

    def test_ssl_scope_is_fixed_broad_and_known_inventory_is_public_and_incomplete(self):
        self.ssl_sync_fixture()
        _, item = self.item('ssl_apply', {'sites': [1]})
        self.assertEqual(item['plan']['required_acknowledgements'], ['ssl_panel_sync'])
        scope = item['plan']['ssl_scope']
        self.assertEqual(scope['policy'], 'baota_acme_panel_sync_v1')
        self.assertTrue(scope['may_update_unselected_sites'])
        self.assertTrue(scope['may_update_orphan_certificates'])
        self.assertTrue(scope['may_update_panel_tls'])
        self.assertTrue(scope['web_service_reload'])
        self.assertFalse(scope['renewal_http_to_https_toggle'])
        inventory = item['before']['ssl_sync_inventory']
        self.assertFalse(inventory['complete'])
        self.assertEqual([v['site_id'] for v in inventory['matching_sites']], [1, 2])
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(item))
        self.assertNotIn('PRIVATE_SECRET', json.dumps(item))
        with self.assertRaises(ValueError):
            self.op('ssl_apply').validate({'sites': [1], 'ssl_scope': {'may_update_panel_tls': False}})

    def test_ssl_known_unselected_inventory_drift_blocks_initial_mutation(self):
        self.ssl_sync_fixture()
        op, item = self.item('ssl_apply', {'sites': [1]})
        self.client.ssl_by_name['unselected.example.com']['csr'] = pem(days=10)
        self.assertEqual(op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_ssl_missing_or_narrowed_scope_cannot_dispatch(self):
        op, item = self.item('ssl_apply', {'sites': [1]})
        for change in ('remove_scope', 'narrow_scope', 'remove_acknowledgement', 'remove_inventory'):
            with self.subTest(change=change):
                altered = copy.deepcopy(item)
                if change == 'remove_scope':
                    altered['plan'].pop('ssl_scope')
                elif change == 'narrow_scope':
                    altered['plan']['ssl_scope']['may_update_panel_tls'] = False
                elif change == 'remove_acknowledgement':
                    altered['plan']['required_acknowledgements'] = []
                else:
                    altered['before'].pop('ssl_sync_inventory')
                self.assertEqual(op.execute(self.client, altered)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_ssl_registered_inventory_limit_is_explicit_and_never_complete(self):
        self.client.ssl.update(status=True, csr=pem(days=5))
        self.client.sites.extend({'id': sid, 'name': 'site-%s.example.com' % sid,
            'path': '/www/wwwroot/site-%s' % sid, 'project_type': 'PHP', 'php_version': '74', 'status': '1'}
            for sid in range(2, 206))
        _, item = self.item('ssl_apply', {'sites': [1]})
        self.assertEqual(item['state'], 'ready')
        inventory = item['before']['ssl_sync_inventory']
        self.assertFalse(inventory['complete'])
        self.assertTrue(inventory['truncated'])
        self.assertEqual(inventory['registered_site_count'], 205)
        self.assertEqual(inventory['scanned_site_count'], 200)
        self.assertEqual(inventory['scan_limit'], 200)
        self.assertEqual(len(inventory['matching_sites']), 200)
        self.assertEqual(inventory['matching_sites'][-1]['site_id'], 200)
        self.assertEqual(sum(c[1] == 'GetSSL' for c in self.client.calls), 201)

    def test_ssl_unselected_inventory_read_failure_blocks_preflight(self):
        self.ssl_sync_fixture()
        original = self.client.request
        def request(path, action=None, params=None, mutating=False, **kwargs):
            if action == 'GetSSL' and params['siteName'] == 'unselected.example.com':
                raise PanelAPIError('unavailable')
            return original(path, action, params, mutating, **kwargs)
        self.client.request = request
        op, item = self.item('ssl_apply', {'sites': [1]})
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_ssl_initial_request_can_auto_deploy_selected_unselected_and_panel(self):
        self.ssl_sync_fixture()
        op, item = self.item('ssl_apply', {'sites': [1]})
        certificate = pem()
        original = self.client.request
        def request(path, action=None, params=None, mutating=False, **kwargs):
            response = original(path, action, params, mutating, **kwargs)
            if action == 'apply_cert_api':
                # Representative official save_cert -> sub_all_cert behavior, without a
                # SetCertToSite request: the first API call itself changes these files.
                self.client.ssl['csr'] = certificate
                self.client.ssl_by_name['unselected.example.com']['csr'] = certificate
                self.client.file_bodies['/www/server/panel/ssl/certificate.pem'] = certificate
                self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
                    'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
                self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = certificate
            return response
        self.client.request = request
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'success')
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.client.ssl_by_name['unselected.example.com']['csr'], certificate)
        self.assertEqual(self.client.file_bodies['/www/server/panel/ssl/certificate.pem'], certificate)
        self.assertNotIn('next_action', result.get('remote', {}))
        self.assertFalse(result['evidence']['panel_sync_outcome_verified'])
        self.assertTrue(result['evidence']['selected_site_configuration_verified'])
        self.assertFalse(result['evidence']['live_tls_verified'])
        self.assertIn('公开证书配置', result['message'])

    def test_ssl_renew_auto_deploys_without_second_mutating_request(self):
        old = self.ssl_sync_fixture()
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = old
        op, item = self.item('ssl_renew', {'sites': [1]})
        certificate = pem()
        original = self.client.request
        def request(path, action=None, params=None, mutating=False, **kwargs):
            response = original(path, action, params, mutating, **kwargs)
            if action == 'renew_cert':
                self.client.ssl['csr'] = certificate
                self.client.ssl_by_name['unselected.example.com']['csr'] = certificate
                self.client.file_bodies['/www/server/panel/ssl/certificate.pem'] = certificate
                self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = certificate
            return response
        self.client.request = request
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'success')
        self.assertEqual([c[1] for c in self.mutations()], ['renew_cert'])
        self.assertNotIn('next_action', result.get('remote', {}))
        self.assertTrue(result['evidence']['selected_site_configuration_verified'])
        self.assertFalse(result['evidence']['live_tls_verified'])
        count = len(self.mutations())
        self.assertEqual(op.reconcile(self.client, item, {'phase': 'issue_and_sync', 'index': 'a' * 32})['state'], 'success')
        self.assertEqual(len(self.mutations()), count)

    def test_ssl_renew_plan_covers_redirect_toggle_and_panel_tls_read_failure_stays_uncertain(self):
        old = self.ssl_sync_fixture()
        self.client.orders = [{'index': 'a' * 32, 'domains': ['example.com'], 'auth_type': 'http',
            'auth_to': '/www/wwwroot/example.com', 'status': 'valid', 'save_path': 'vhost/letsencrypt/example.com'}]
        self.client.file_bodies['/www/server/panel/vhost/letsencrypt/example.com/fullchain.pem'] = old
        op, item = self.item('ssl_renew', {'sites': [1]})
        self.assertTrue(item['plan']['ssl_scope']['renewal_http_to_https_toggle'])
        original = self.client.request
        submitted = False
        def request(path, action=None, params=None, mutating=False, **kwargs):
            nonlocal submitted
            if submitted:
                raise PanelAPIError('面板 HTTPS 证书验证失败')
            response = original(path, action, params, mutating, **kwargs)
            submitted = submitted or mutating
            return response
        self.client.request = request
        result = op.execute(self.client, item)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['remote']['phase'], 'issue_and_sync')
        self.assertNotIn('next_action', result['remote'])
        self.assertEqual(len(self.mutations()), 1)
        op.reconcile(self.client, item, result['remote'])
        self.assertEqual(len(self.mutations()), 1)
