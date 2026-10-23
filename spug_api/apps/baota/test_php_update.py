"""PHP branch update contracts against an entirely local official-protocol fixture."""
import copy
import importlib.util
import posixpath
from unittest import TestCase
from unittest.mock import patch

from . import resources as r
from .client import PanelAPIError
from .test_operations import PanelFixture


class PHPUpdateFixture(PanelFixture):
    def __init__(self):
        super().__init__()
        self.actual_version = '7.4.32'
        self.actual_bin = '/www/server/php/74/bin/php'
        self.actual_ini = '/www/server/php/74/etc/php.ini'
        self.attribute_overrides = {}
        self.software['php-7.4'] = {
            'name': 'php-7.4', 'setup': True, 'version': '7.4.32', 'type': 5,
            'price': 0, 'dependnet': [], 'mutex': '',
            'versions': [{'m_version': '7.4', 'version': '33', 'cpu_limit': 0,
                          'mem_limit': 0, 'os_limit': 0, 'dependnet': ''}]}

    def request(self, path, action=None, params=None, mutating=False, **kwargs):
        if action in ('php_info', 'get_file_attribute') or (action == 'install_plugin' and 'upgrade' in (params or {})):
            p = copy.deepcopy(params or {})
            self.calls.append((path, action, p, mutating, kwargs))
            if mutating and self.mutation_error:
                raise self.mutation_error
            if action == 'php_info':
                return {'phpinfo': {'php_version': self.actual_version, 'php_path': '/www/server/php/',
                                    'php_bin': self.actual_bin, 'php_ini': self.actual_ini,
                                    'ini': {'private_secret': 'SECRET'}}}
            if action == 'get_file_attribute':
                filename = p['filename']
                return {'name': posixpath.basename(filename), 'path': posixpath.dirname(filename),
                        'is_dir': not filename.endswith('/bin/php'), 'is_link': False,
                        'st_ino': len(filename) * 100, 'st_dev': 1,
                        **self.attribute_overrides.get(filename, {})}
            self.tasks.append({'id': len(self.tasks) + 1, 'name': 'upgrade[php-' + p['version'] + ']', 'status': '0'})
            return {'status': True, 'msg_id': 999, 'private_key': 'SECRET'}
        return super().request(path, action, params, mutating, **kwargs)


class PHPUpdateTests(TestCase):
    def setUp(self):
        module_name = 'apps.baota.operations.php_update'
        exists = importlib.util.find_spec(module_name)
        self.assertIsNotNone(exists, 'PHP 同分支更新适配器尚未实现')
        self.module = importlib.import_module(module_name)
        self.op = self.module.UpdatePHP()
        self.client = PHPUpdateFixture()
        self.params = {'php_version': '74', 'm_version': '7.4', 'min_version': '33', 'install_type': '0'}

    def item(self):
        return self.op.preflight(self.client, self.params)['items'][0]

    def mutations(self):
        return [call for call in self.client.calls if call[3]]

    def test_params_bind_branch_to_numeric_target_and_compile_update(self):
        self.assertEqual(self.op.validate(self.params), self.params)
        for change in ({'php_version': '75'}, {'m_version': '8.0'}, {'min_version': '33;id'},
                       {'min_version': '033'}, {'install_type': '1'}, {'force': True}, {'name': 'nginx'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.op.validate({**self.params, **change})

    def test_preflight_freezes_actual_version_catalog_and_website_impact(self):
        item = self.item()
        self.assertEqual(item['state'], 'ready')
        self.assertEqual(item['plan']['current_version'], '7.4.32')
        self.assertEqual(item['plan']['target_version'], '7.4.33')
        self.assertEqual(item['plan']['impact']['website_refs'][0]['name'], 'example.com')
        self.assertTrue(item['plan']['impact']['php_service_may_restart'])
        self.assertFalse(item['plan']['impact']['patch_pinned'])
        self.assertIn('catalog_digest', item['before']['software'])
        self.assertNotIn('SECRET', str(item))
        self.assertEqual(self.mutations(), [])
        self.assertNotIn('GetPHPVersion', [c[1] for c in self.client.calls])

    def test_three_official_panel_versions_use_same_contract(self):
        with patch.object(r, 'SUPPORTED_VERSIONS', r.SUPPORTED_VERSIONS | {'9.2.0'}):
            for version in ('9.2.0', '11.7.0', '13.1.0'):
                with self.subTest(version=version):
                    self.client.info['version'] = version
                    self.assertEqual(self.item()['state'], 'ready')

    def test_cached_catalog_version_does_not_define_actual_installed_version(self):
        self.client.software['php-7.4']['version'] = '7.4.33'
        self.assertEqual(self.item()['plan']['current_version'], '7.4.32')

    def test_noop_checks_actual_version_and_never_dispatches(self):
        self.client.actual_version = '7.4.33'
        item = self.item()
        self.assertEqual(item['state'], 'already')
        self.assertEqual(self.op.execute(self.client, item)['state'], 'already')
        self.assertEqual(self.mutations(), [])

    def test_reject_uninstalled_downgrade_unknown_target_and_nonlatest_catalog(self):
        for change in ('uninstalled', 'downgrade', 'unknown', 'nonlatest', 'sync_plugin'):
            with self.subTest(change=change):
                self.client = PHPUpdateFixture()
                software = self.client.software['php-7.4']
                if change == 'uninstalled': software['setup'] = False
                if change == 'downgrade': self.client.actual_version = '7.4.34'
                if change == 'unknown': software['versions'][0]['version'] = '31'
                if change == 'nonlatest': software['versions'].append({**software['versions'][0], 'version': '34'})
                if change == 'sync_plugin': software['type'] = 8
                self.assertEqual(self.item()['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_fail_closed_on_missing_wrong_branch_or_untrusted_binary_identity(self):
        for kind in ('missing_version', 'branch', 'suffix', 'binary', 'ini', 'symlink', 'missing_dir', 'binary_dir'):
            with self.subTest(kind=kind):
                self.client = PHPUpdateFixture()
                if kind == 'missing_version': self.client.actual_version = None
                if kind == 'branch': self.client.actual_version = '8.0.32'
                if kind == 'suffix': self.client.actual_version = '7.4.32-custom'
                if kind == 'binary': self.client.actual_bin = '/tmp/php'
                if kind == 'ini': self.client.actual_ini = '/www/server/php/80/etc/php.ini'
                if kind == 'symlink': self.client.attribute_overrides['/www/server/php/74'] = {'is_link': True}
                if kind == 'missing_dir': self.client.attribute_overrides['/www/server/php/74'] = {'is_dir': False}
                if kind == 'binary_dir': self.client.attribute_overrides['/www/server/php/74/bin/php'] = {'is_dir': True}
                self.assertEqual(self.item()['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_requirements_ols_and_existing_php_task_block_update(self):
        for kind in ('memory', 'dependency', 'license', 'ols', 'queue'):
            with self.subTest(kind=kind):
                self.client = PHPUpdateFixture()
                software = self.client.software['php-7.4']
                if kind == 'memory': software['versions'][0]['mem_limit'] = 999999
                if kind == 'dependency': software['dependnet'] = 'missing'
                if kind == 'license': software['price'] = 100
                if kind == 'ols': self.client.webserver = 'openlitespeed'
                if kind == 'queue': self.client.tasks = [{'id': 1, 'name': '安装[php-8.0]', 'status': '0'}]
                self.assertEqual(self.item()['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_dispatch_uses_official_update_marker_once_without_bypasses(self):
        item = self.item()
        output = self.op.execute(self.client, item)
        self.assertEqual(output['state'], 'remote')
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.mutations()[0][:4], ('/plugin', 'install_plugin', {
            'sName': 'php-7.4', 'version': '7.4', 'min_version': '33', 'type': '0', 'upgrade': '1'}, True))

    def test_dispatch_rechecks_catalog_actual_version_paths_refs_resources_and_queue(self):
        for kind in ('catalog', 'actual', 'path', 'refs', 'resource', 'queue', 'panel'):
            with self.subTest(kind=kind):
                self.client = PHPUpdateFixture()
                item = self.item()
                if kind == 'catalog': self.client.software['php-7.4']['versions'][0]['version'] = '34'
                if kind == 'actual': self.client.actual_version = '7.4.31'
                if kind == 'path': self.client.actual_bin = '/tmp/php'
                if kind == 'refs': self.client.sites[0]['path'] = '/www/wwwroot/moved'
                if kind == 'resource': self.client.info['memTotal'] = 1; self.client.software['php-7.4']['versions'][0]['mem_limit'] = 3
                if kind == 'queue': self.client.tasks.append({'id': 1, 'name': 'upgrade[php-7.4]', 'status': '0'})
                if kind == 'panel': self.client.info['version'] = '11.7.0'
                self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_directory_replacement_and_missing_inode_identity_are_blocked(self):
        item = self.item()
        self.client.attribute_overrides['/www/server/php/74'] = {'st_ino': 987654321}
        self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])
        self.client = PHPUpdateFixture()
        self.client.attribute_overrides['/www/server/php/74'] = {'st_ino': None}
        self.assertEqual(self.item()['state'], 'failed')

    def test_unknown_php_site_reference_prevents_incomplete_impact_preview(self):
        self.client.sites[0]['php_version'] = None
        self.assertEqual(self.item()['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_restart_ack_is_frozen_in_plan_and_cannot_be_removed_before_dispatch(self):
        item = self.item()
        self.assertEqual(item['plan'].get('required_acknowledgements'), ['php_update_restart'])
        for replacement in (None, [], ['ssl_panel_sync']):
            with self.subTest(replacement=replacement):
                altered = copy.deepcopy(item)
                if replacement is None:
                    altered['plan'].pop('required_acknowledgements', None)
                else:
                    altered['plan']['required_acknowledgements'] = replacement
                self.assertEqual(self.op.execute(self.client, altered)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_runtime_readback_succeeds_despite_stale_cached_version(self):
        item = self.item()
        self.client.actual_version = '7.4.33'
        self.client.tasks = [{'id': 1, 'name': 'upgrade[php-7.4]', 'status': '1'}]
        output = self.op.reconcile(self.client, item, {'phase': 'apply', 'task_ids': [1]})
        self.assertEqual(output['state'], 'success')
        self.assertEqual(output['evidence']['version'], '7.4.33')
        self.assertNotIn('SECRET', str(output))
        self.assertEqual(self.mutations(), [])

    def test_readback_does_not_claim_success_after_branch_directory_replacement(self):
        item = self.item()
        self.client.actual_version = '7.4.33'
        self.client.attribute_overrides['/www/server/php/74'] = {'st_ino': 987654321}
        self.assertEqual(self.op.reconcile(self.client, item, {'phase': 'apply'})['state'], 'uncertain')
        self.assertEqual(self.mutations(), [])

    def test_ambiguous_terminal_queue_stays_uncertain_even_when_actual_target_is_observed(self):
        item = self.item()
        self.client.tasks = [{'id': 1, 'name': 'upgrade[php-7.4]', 'status': '1'}]
        remote = {'phase': 'apply', 'ambiguous': True}
        self.assertEqual(self.op.reconcile(self.client, item, remote)['state'], 'uncertain')
        self.client.actual_version = '7.4.33'
        self.assertEqual(self.op.reconcile(self.client, item, remote)['state'], 'uncertain')
        self.assertEqual(self.mutations(), [])

    def test_actual_target_cannot_finish_before_unique_new_update_queue_is_terminal(self):
        item = self.item()
        self.client.actual_version = '7.4.33'
        self.assertEqual(self.op.reconcile(self.client, item, {})['state'], 'remote')
        self.client.tasks = [{'id': 1, 'name': 'upgrade[php-7.4]', 'status': '0'}]
        self.assertEqual(self.op.reconcile(self.client, item, {})['state'], 'remote')
        self.client.tasks.append({'id': 2, 'name': 'upgrade[php-7.4]', 'status': '1'})
        self.assertEqual(self.op.reconcile(self.client, item, {})['state'], 'uncertain')
        self.assertEqual(self.mutations(), [])

    def test_later_same_target_queue_collision_is_not_hidden_by_saved_task_id(self):
        for later_status in ('0', '1'):
            with self.subTest(later_status=later_status):
                self.client = PHPUpdateFixture()
                item = self.item()
                submitted = self.op.execute(self.client, item)
                self.assertEqual(submitted['remote']['task_ids'], [1])
                self.client.tasks[0]['status'] = '1'
                self.client.actual_version = '7.4.33'
                self.client.tasks.append({'id': 2, 'name': 'upgrade[php-7.4]', 'status': later_status})
                output = self.op.reconcile(self.client, item, submitted['remote'])
                self.assertEqual(output['state'], 'uncertain')
                self.assertTrue(output['remote'].get('collision'))
                self.assertEqual(len(self.mutations()), 1)

    def test_preflight_old_target_queue_reactivated_during_update_blocks_success(self):
        self.client.tasks = [{'id': 10, 'name': 'upgrade[php-7.4]', 'status': '1'}]
        item = self.item()
        submitted = self.op.execute(self.client, item)
        self.client.tasks[0]['status'] = '0'
        self.client.tasks[1]['status'] = '1'
        self.client.actual_version = '7.4.33'
        self.assertEqual(self.op.reconcile(self.client, item, submitted['remote'])['state'], 'uncertain')
        self.assertEqual(len(self.mutations()), 1)

    def test_saved_target_queue_cannot_be_reassociated_after_it_disappears(self):
        item = self.item()
        submitted = self.op.execute(self.client, item)
        self.client.tasks = [{'id': 2, 'name': 'upgrade[php-7.4]', 'status': '1'}]
        self.client.actual_version = '7.4.33'
        self.assertEqual(self.op.reconcile(self.client, item, submitted['remote'])['state'], 'uncertain')
        self.assertEqual(len(self.mutations()), 1)

    def test_observed_queue_collision_stays_uncertain_if_conflicting_row_disappears(self):
        item = self.item()
        submitted = self.op.execute(self.client, item)
        self.client.tasks[0]['status'] = '1'
        self.client.actual_version = '7.4.33'
        self.client.tasks.append({'id': 2, 'name': 'upgrade[php-7.4]', 'status': '0'})
        collision = self.op.reconcile(self.client, item, submitted['remote'])
        self.assertEqual(collision['state'], 'uncertain')
        self.client.tasks.pop()
        self.assertEqual(self.op.reconcile(self.client, item, collision['remote'])['state'], 'uncertain')
        self.assertEqual(len(self.mutations()), 1)

    def test_finished_queue_and_cached_target_are_insufficient_for_success(self):
        item = self.item()
        self.client.software['php-7.4']['version'] = '7.4.33'
        self.client.tasks = [{'id': 1, 'name': 'upgrade[php-7.4]', 'status': '1'}]
        self.assertEqual(self.op.reconcile(self.client, item, {'phase': 'apply'})['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_different_actual_version_and_queue_collision_remain_uncertain(self):
        for version in ('7.4.34', '7.4.31', '8.0.33'):
            with self.subTest(version=version):
                self.client = PHPUpdateFixture()
                item = self.item()
                self.client.actual_version = version
                self.assertEqual(self.op.reconcile(self.client, item, {})['state'], 'uncertain')
        self.client = PHPUpdateFixture()
        item = self.item()
        self.client.tasks = [{'id': i, 'name': 'upgrade[php-7.4]', 'status': '0'} for i in (1, 2)]
        self.assertEqual(self.op.reconcile(self.client, item, {})['state'], 'uncertain')

    def test_ambiguous_result_cannot_redispatch_and_reads_do_not_repeat_mutation(self):
        item = self.item()
        self.client.mutation_error = PanelAPIError('secret body', uncertain=True)
        output = self.op.execute(self.client, item)
        self.assertEqual(output['state'], 'uncertain')
        self.assertEqual(len(self.mutations()), 1)
        self.op.reconcile(self.client, item, output['remote'])
        self.op.dispatch(self.client, item, 'apply', output['remote'])
        self.assertEqual(len(self.mutations()), 1)

    def test_update_options_only_offer_installed_branch_and_precise_newer_latest_patch(self):
        self.client.software['php-7.4']['version'] = '7.4.33'
        output = self.module.update_options(self.client)
        self.assertEqual(len(output['php_updates']), 1)
        option = output['php_updates'][0]
        self.assertEqual({k: option[k] for k in ('php_version', 'm_version', 'min_version', 'current_version', 'target_version')},
                         {'php_version': '74', 'm_version': '7.4', 'min_version': '33',
                          'current_version': '7.4.32', 'target_version': '7.4.33'})
        self.assertEqual(option['install_types'], ['0'])
        self.client.actual_version = '7.4.34'
        self.assertEqual(self.module.update_options(self.client), {'php_updates': []})
        self.assertEqual(self.mutations(), [])
