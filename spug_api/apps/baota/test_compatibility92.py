"""9.2 integration boundaries using the audited Linux protocol shapes."""
import copy
from unittest import TestCase

from . import resources as r
from .client import PanelAPIError
from .operations import get_operation
from .operations.software import loaded_modules
from .test_operations import PanelFixture


class Compatibility92Tests(TestCase):
    def setUp(self):
        self.client = PanelFixture()
        self.client.info['version'] = '9.2.0'

    def test_recognizes_92_but_unknown_and_windows_still_fail(self):
        self.assertEqual(r.system_info(self.client)['version'], '9.2.0')
        for version, system in [('9.2.1', 'Ubuntu 24'), ('99.0.0', 'Linux'), ('9.2.0', 'Windows')]:
            self.client.info.update(version=version, system=system)
            with self.assertRaises(PanelAPIError):
                r.system_info(self.client)
        self.assertFalse(any(call[3] for call in self.client.calls))

    def test_92_site_and_basedir_use_audited_protocol_and_readback(self):
        op = get_operation('site_add')
        item = op.preflight(self.client, {'entries': [{'domains': ['new.example.com'],
            'path': '/www/wwwroot/new.example.com', 'php_version': '74', 'port': 80}]})['items'][0]
        self.assertEqual(item['state'], 'ready')
        self.assertEqual(op.execute(self.client, item)['state'], 'success')
        op = get_operation('site_basedir')
        item = op.preflight(self.client, {'sites': [1], 'enabled': True})['items'][0]
        self.assertEqual(op.execute(self.client, item)['state'], 'success')

    def test_92_rewrite_file_result_keeps_runtime_uncertain(self):
        op = get_operation('site_rewrite')
        item = op.preflight(self.client, {'sites': [1], 'webserver': 'nginx',
            'mode': 'custom', 'rules': 'location / { try_files $uri $uri/ /index.php; }'})['items'][0]
        self.assertEqual(item['state'], 'ready')
        self.assertEqual(op.execute(self.client, item)['state'], 'uncertain')

    def test_software_title_and_execution_scope_are_explicit(self):
        data = copy.deepcopy(self.client.software['nginx'])
        data['title'] = 'Nginx 网站服务器'
        option = r.software_option(data)
        self.assertEqual(option['title'], 'Nginx 网站服务器')
        self.assertTrue(option['install_supported'])
        data.update(type=1, title='Plugin')
        self.assertFalse(r.software_option(data)['install_supported'])
        data.update(type=5, price=99)
        self.assertFalse(r.software_option(data)['install_supported'])

    def test_sync_plugin_is_rejected_before_mutation(self):
        self.client.software['nginx']['type'] = 1
        op = get_operation('software_install')
        item = op.preflight(self.client, {'name': 'nginx', 'm_version': '1.24', 'min_version': '0'})['items'][0]
        self.assertEqual(item['state'], 'failed')
        self.assertIn('protocol', item['plan']['blockers'])
        op.execute(self.client, item)
        self.assertFalse(any(call[3] for call in self.client.calls))

    def test_php_modules_require_actual_matching_binary_ini_and_patch(self):
        original = self.client.request
        response = {'phpinfo': {'modules': 'redis', 'php_version': '7.4.33',
            'php_bin': '/www/server/php/74/bin/php', 'php_ini': '/www/server/php/74/etc/php.ini'}}
        self.client.request = lambda path, action=None, *args, **kwargs: (
            copy.deepcopy(response) if action == 'php_info' else original(path, action, *args, **kwargs))
        self.assertEqual(loaded_modules(self.client, '74'), {'redis'})
        for field, value in [('php_bin', '/www/server/php/80/bin/php'),
                             ('php_ini', '/www/server/php/80/etc/php/80/litespeed/php.ini'),
                             ('php_version', '8.0.30')]:
            bad = copy.deepcopy(response)
            bad['phpinfo'][field] = value
            self.client.request = lambda *args, **kwargs: copy.deepcopy(bad)
            with self.assertRaises(PanelAPIError):
                loaded_modules(self.client, '74')
