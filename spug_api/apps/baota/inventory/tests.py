"""Inventory fixtures never contact a customer panel."""
import copy
import importlib
import json
import os
import subprocess
import sys
import threading
from datetime import timedelta
from unittest import TestCase
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.urls import resolve
from django.utils import timezone

from apps.baota import models
from apps.baota import tests as legacy


SITE = {'id': 1, 'name': 'example.com', 'status': '1', 'php_version': '82',
        'ssl': {'notAfter': '2027-01-01', 'private_key': 'SECRET'}, 'password': 'SECRET'}
DOMAINS = [{'id': 1, 'pid': 1, 'name': 'example.com', 'port': 80},
           {'id': 2, 'pid': 1, 'name': 'www.example.com', 'port': '443'}]
BACKUP = {'id': 9, 'pid': 1, 'name': 'web_example.tar.gz',
          'filename': '/www/backup/web_example.tar.gz|https://cloud.example/?secret=SECRET',
          'local': '/www/backup/web_example.tar.gz', 'localexist': 1, 'backup_status': 0,
          'size': 1234, 'addtime': '2026-10-24 10:00:00', 'password': 'SECRET'}


class PanelFixture:
    def __init__(self):
        self.calls = []
        self.data = {'sites': [copy.deepcopy(SITE)], 'domain': copy.deepcopy(DOMAINS),
                     'databases': [{'id': 1, 'name': 'db_one', 'password': 'SECRET',
                                    'conn_config': {'secret': 'SECRET'}}, {'id': 2, 'name': 'db_empty'}]}
        self.backups = {('0', '1'): [copy.deepcopy(BACKUP)], ('1', '1'): [copy.deepcopy(BACKUP)]}

    def request(self, path, action, params=None, **kwargs):
        self.calls.append((path, action, copy.deepcopy(params), kwargs))
        if (path, action) == ('/system', 'GetSystemTotal'):
            return {'version': '13.1.0', 'system': 'Ubuntu 22.04 LTS x86_64'}
        if (path, action) != ('/data', 'getData'):
            raise AssertionError('Unexpected panel request')
        if params['table'] == 'backup':
            return {'data': copy.deepcopy(self.backups.get((str(params['type']), str(params['search'])), []))}
        start = (params['p'] - 1) * params['limit']
        return {'data': copy.deepcopy(self.data[params['table']][start:start + params['limit']])}


class InventoryReaderTests(TestCase):
    def readers(self):
        self.assertTrue(hasattr(models, 'PanelInventorySnapshot'), 'inventory models missing')
        return importlib.import_module('apps.baota.inventory.readers')

    def test_sites_collect_all_domain_pages_and_keep_only_whitelisted_fields(self):
        readers, client = self.readers(), PanelFixture()
        with patch.object(readers, 'PAGE_SIZE', 1):
            rows = readers.read_inventory(client, 'sites')
        self.assertEqual(rows[0]['domains'], [{'name': 'example.com', 'port': 80},
                                              {'name': 'www.example.com', 'port': 443}])
        self.assertEqual(rows[0]['site_status'], 'running')
        self.assertEqual(rows[0]['expires_at'], '2027-01-01')
        self.assertNotIn('SECRET', json.dumps(rows))
        self.assertNotIn('path', rows[0])
        self.assertFalse(any(call[1] in ('GetSSL', 'get_file_attribute') for call in client.calls))

    def test_backups_preserve_assets_without_records_and_do_not_expose_cloud_urls(self):
        readers, client = self.readers(), PanelFixture()
        rows = readers.read_inventory(client, 'backups')
        self.assertEqual([(r['asset_type'], r['asset_id']) for r in rows], [('site', 1), ('database', 1), ('database', 2)])
        self.assertEqual([r['state'] for r in rows], ['panel_reported', 'panel_reported', 'no_record'])
        self.assertEqual(rows[0]['backup_name'], 'web_example.tar.gz')
        self.assertEqual(rows[0]['location'], 'cloud_record')
        for text in ('SECRET', 'cloud.example', '/www/', 'conn_config', 'password'):
            self.assertNotIn(text, json.dumps(rows))
        calls = [call[2] for call in client.calls if call[2] and call[2]['table'] == 'backup']
        self.assertEqual([(c['search'], c['type'], c['limit']) for c in calls], [(1, '0', 1), (1, '1', 1), (2, '1', 1)])

    def test_backup_states_preserve_unknown_and_in_progress_before_missing_or_empty(self):
        readers = self.readers()
        for changes, expected in [({'localexist': 0}, 'local_missing'),
                                  ({'size': 0}, 'empty'),
                                  ({'backup_status': 1, 'localexist': 0, 'size': 0}, 'in_progress'),
                                  ({'localexist': None}, 'unknown'),
                                  ({'backup_status': None}, 'unknown'),
                                  ({'size': 'n/a'}, 'unknown')]:
            with self.subTest(changes=changes):
                client = PanelFixture()
                client.backups[('0', '1')][0].update(changes)
                self.assertEqual(readers.read_inventory(client, 'backups')[0]['state'], expected)

    def test_malformed_domain_and_duplicate_pages_fail_instead_of_claiming_complete_inventory(self):
        readers = self.readers()
        for changes in ({'name': 'https://example.com/?SECRET'}, {'port': 'secret'}, {'pid': -1}):
            client = PanelFixture()
            client.data['domain'][0].update(changes)
            with self.assertRaises(readers.InventoryError):
                readers.read_inventory(client, 'sites')
        client = PanelFixture()
        client.data['sites'].append(copy.deepcopy(SITE))
        with self.assertRaises(readers.InventoryError):
            readers.read_inventory(client, 'sites')

    def test_request_count_and_total_time_budgets_stop_before_additional_requests(self):
        readers, client = self.readers(), PanelFixture()
        with patch.object(readers, 'MAX_REQUESTS', 2):
            with self.assertRaises(readers.InventoryError):
                readers.read_inventory(client, 'sites')
        self.assertEqual(len(client.calls), 2)
        client = PanelFixture()
        with patch.object(readers.time, 'monotonic', side_effect=[0, 50]):
            with self.assertRaises(readers.InventoryError):
                readers.read_inventory(client, 'sites')
        self.assertEqual(client.calls, [])

    def test_worker_timeout_kills_and_reaps_before_error_returns(self):
        readers = self.readers()
        child = importlib.import_module('unittest.mock').Mock()
        child.communicate.side_effect = subprocess.TimeoutExpired('private worker', 45)
        with patch.object(readers.subprocess, 'Popen', return_value=child):
            with self.assertRaises(readers.InventoryError) as raised:
                readers.run_worker({'kind': 'sites'})
        self.assertEqual(raised.exception.code, 'timeout')
        child.kill.assert_called_once()
        child.wait.assert_called_once()

    def test_real_worker_entrypoint_imports_and_sanitizes_configuration_without_network(self):
        readers = self.readers()
        payload = {'kind': 'sites', 'api_base_url': 'INVALID SECRET URL', 'api_key': 'SECRET',
                   'tls_ca': '', 'tls_fingerprint': ''}
        self.assertRaises(readers.InventoryError, readers.run_worker, payload)

    def test_real_worker_fixture_normalizes_success_without_django_database_or_network(self):
        self.readers()
        bootstrap = '''
import apps.baota.inventory.worker as worker
class Fixture:
    def __init__(self, panel): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def request(self, path, action, params=None, **kwargs):
        if action == 'GetSystemTotal':
            return {'version':'13.1.0','system':'Ubuntu Linux'}
        if params['table'] == 'sites':
            return {'data':[{'id':1,'name':'example.com','ssl':-1,'password':'SECRET'}]}
        return {'data':[{'id':1,'pid':1,'name':'example.com','port':80,'private_key':'SECRET'}]}
worker.PanelClient = Fixture
worker.main()
'''
        env = dict(os.environ)
        env.pop('DJANGO_SETTINGS_MODULE', None)
        result = subprocess.run([sys.executable, '-c', bootstrap],
                                input=json.dumps({'kind': 'sites', 'api_base_url': '', 'api_key': 'SECRET',
                                                  'tls_ca': '', 'tls_fingerprint': ''}),
                                text=True, capture_output=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)['rows']
        self.assertEqual(rows[0]['domains'], [{'name': 'example.com', 'port': 80}])
        self.assertNotIn('SECRET', result.stdout)


class InventoryAPITests(TransactionTestCase):
    setUp = legacy.BaotaAPITests.setUp
    make_user = legacy.BaotaAPITests.make_user
    authenticate = legacy.BaotaAPITests.authenticate
    request = legacy.BaotaAPITests.request
    group = legacy.BaotaAPITests.group
    panel = legacy.BaotaAPITests.panel

    def service(self):
        self.assertTrue(hasattr(models, 'PanelInventorySnapshot'), 'inventory models missing')
        return importlib.import_module('apps.baota.inventory.service')

    def configured(self, **changes):
        return models.Panel.objects.get(pk=self.panel(api_base_url='https://api.example.com',
                                                     api_key='fixture-api-key', **changes)['id'])

    def collect(self, panel, kind='sites'):
        return self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': kind})

    def fake_collect(self, kind='sites', fixture=None):
        readers = importlib.import_module('apps.baota.inventory.readers')
        return readers.read_inventory(fixture or PanelFixture(), kind)

    def expire_cooldown(self):
        models.PanelInventorySnapshot.objects.update(cooldown_until=timezone.now() - timedelta(seconds=1))

    def test_options_and_empty_gets_use_only_database_and_no_secrets(self):
        service = self.service()
        panel = self.configured()
        with patch.object(service, 'collect_panel') as collector:
            options = self.request('get', '/baota/inventory/options/')
            self.assertEqual(options['panels'][0]['id'], panel.pk)
            self.assertTrue(options['panels'][0]['configured'])
            for kind in ('sites', 'backups'):
                result = self.request('get', '/baota/inventory/' + kind + '/')
                self.assertEqual(result['items'], [])
                self.assertEqual(result['panels'][0]['status'], 'never_collected')
            collector.assert_not_called()
        self.assertFalse(models.PanelInventorySnapshot.objects.exists())
        self.assertFalse(models.PanelInventoryLease.objects.exists())
        self.assertNotIn('fixture-api-key', json.dumps(options))
        self.assertNotIn('secure-entry', json.dumps(options))

    def test_all_endpoints_reject_non_superadmin_even_with_panel_permissions(self):
        self.service()
        panel = self.configured()
        self.authenticate(self.make_user('reader', perms={'panel': ['view', 'edit']}))
        for endpoint in ('options', 'sites', 'backups'):
            self.request('get', '/baota/inventory/' + endpoint + '/', success=False)
        self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': 'sites'}, success=False)

    def test_manual_collection_cache_failure_preservation_and_get_never_recollects(self):
        service, panel = self.service(), self.configured()
        with patch.object(service, 'collect_panel', return_value=self.fake_collect()):
            self.assertEqual(self.collect(panel)['status'], 'ok')
        self.expire_cooldown()
        with patch.object(service, 'collect_panel', side_effect=RuntimeError('SECRET remote body')):
            result = self.collect(panel)
        self.assertEqual(result['status'], 'stale')
        with patch.object(service, 'collect_panel') as collector:
            result = self.request('get', '/baota/inventory/sites/')
            collector.assert_not_called()
        self.assertEqual(len(result['items']), 1)
        self.assertTrue(result['items'][0]['stale'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertIsNotNone(result['panels'][0]['sampled_at'])

    def test_configuration_change_invalidates_and_during_collection_cannot_save_fresh_snapshot(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect()
        with patch.object(service, 'collect_panel', return_value=rows):
            self.collect(panel)
        revision = panel.api_config_revision
        models.Panel.objects.filter(pk=panel.pk).update(api_config_revision=revision + 1)
        result = self.request('get', '/baota/inventory/sites/')
        self.assertEqual(result['panels'][0]['status'], 'invalidated')
        self.assertEqual(result['items'], [])
        self.expire_cooldown()
        def drift(*args):
            models.Panel.objects.filter(pk=panel.pk).update(api_config_revision=revision + 2)
            return rows
        with patch.object(service, 'collect_panel', side_effect=drift):
            self.assertEqual(self.collect(panel)['status'], 'invalidated')
        self.assertEqual(models.PanelInventorySnapshot.objects.get(panel=panel).config_revision, revision)

    def test_cleared_api_hides_prior_assets_while_preserving_successful_storage(self):
        service, panel = self.service(), self.configured()
        with patch.object(service, 'collect_panel', return_value=self.fake_collect()):
            self.collect(panel)
        models.Panel.objects.filter(pk=panel.pk).update(api_key_ciphertext='')
        result = self.request('get', '/baota/inventory/sites/')
        self.assertEqual(result['items'], [])
        self.assertEqual(result['panels'][0]['status'], 'unconfigured')
        self.assertTrue(result['panels'][0]['stale'])
        self.assertEqual(len(models.PanelInventorySnapshot.objects.get(panel=panel).rows), 1)

    def test_cooldown_global_lease_and_exception_release(self):
        service, panel = self.service(), self.configured()
        with patch.object(service, 'collect_panel', return_value=self.fake_collect()) as collector:
            self.collect(panel)
            self.assertEqual(self.collect(panel)['status'], 'cooldown')
            self.assertEqual(collector.call_count, 1)
            self.expire_cooldown()
            lease = service.acquire_lease(timezone.now())
            self.assertIsNotNone(lease)
            self.assertEqual(self.collect(panel)['status'], 'busy')
            self.assertEqual(collector.call_count, 1)
            service.release_lease(lease)
        self.expire_cooldown()
        with patch.object(service, 'collect_panel', side_effect=RuntimeError('SECRET')):
            self.collect(panel)
        self.assertIsNotNone(service.acquire_lease(timezone.now()))

    def test_ssl_filters_pagination_group_descendants_and_stale_age(self):
        service = self.service()
        root = self.group('root')
        child = self.group('child', root['id'])
        panel = self.configured(group_id=child['id'])
        rows = self.fake_collect()
        tomorrow = (timezone.now().date() + timedelta(days=1)).isoformat()
        rows[0]['expires_at'] = tomorrow
        rows.append({**copy.deepcopy(rows[0]), 'site_id': 2, 'name': 'another.example',
                     'ssl_state': 'unconfigured', 'expires_at': ''})
        with patch.object(service, 'collect_panel', return_value=rows):
            self.collect(panel)
        result = self.request('get', '/baota/inventory/sites/', {'group_id': root['id'], 'state': 'expiring', 'page_size': 1})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['summary']['total'], 2)
        self.assertEqual(result['items'][0]['days_remaining'], 1)
        self.assertEqual(self.request('get', '/baota/inventory/sites/', {'q': 'www.example.com'})['total'], 2)
        models.PanelInventorySnapshot.objects.update(sampled_at=timezone.now() - timedelta(hours=25))
        self.assertTrue(self.request('get', '/baota/inventory/sites/')['items'][0]['stale'])

    def test_backups_threshold_is_dynamic_and_unknown_time_not_overdue(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect('backups')
        rows[0]['backup_at'] = (timezone.now() - timedelta(hours=50)).isoformat()
        rows[1]['backup_at'] = None
        with patch.object(service, 'collect_panel', return_value=rows):
            self.collect(panel, 'backups')
        result = self.request('get', '/baota/inventory/backups/', {'state': 'overdue'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(self.request('get', '/baota/inventory/backups/', {'state': 'overdue', 'max_age_hours': 72})['total'], 0)
        result = self.request('get', '/baota/inventory/backups/')
        self.assertIsNone(result['items'][1]['overdue'])
        self.assertIsNone(result['items'][2]['overdue'])

    def test_strict_validation_nonatomic_and_crypto_errors_are_sanitized(self):
        service, panel = self.service(), self.configured()
        for values in ({'page_size': 101}, {'page': 0}, {'q': 'x' * 201}, {'state': 'invalid'}, {'path': '/secret'}):
            self.request('get', '/baota/inventory/sites/', values, success=False)
        self.request('get', '/baota/inventory/backups/', {'max_age_hours': 49}, success=False)
        self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': 'invalid'}, success=False)
        self.assertIn('default', getattr(resolve('/baota/inventory/collect/').func, '_non_atomic_requests', set()))
        with patch.object(service, 'collect_panel', side_effect=ValueError('SECRET crypto details')):
            self.assertNotIn('SECRET', json.dumps(self.collect(panel)))

    def test_admin_disabled_during_collection_cannot_save_snapshot(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect()
        def disabled(*args):
            self.assertFalse(connection.in_atomic_block, 'network must not hold a DB transaction')
            type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
            return rows
        with patch.object(service, 'collect_panel', side_effect=disabled):
            self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': 'sites'}, success=False)
        self.assertFalse(models.PanelInventorySnapshot.objects.exists())
        self.assertIsNotNone(service.acquire_lease(timezone.now()))

    def test_soft_deleted_superadmin_cannot_read_or_collect(self):
        service, panel = self.service(), self.configured()
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_deleted=True, is_active=True)
        with patch.object(service, 'collect_panel') as collector:
            for endpoint in ('options', 'sites', 'backups'):
                self.request('get', '/baota/inventory/' + endpoint + '/', success=False)
            self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': 'sites'}, success=False)
        collector.assert_not_called()

    def test_soft_deleted_superadmin_during_collection_cannot_save_snapshot(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect()
        def deleted(*args):
            type(self.admin).objects.filter(pk=self.admin.pk).update(is_deleted=True, is_active=True)
            return rows
        with patch.object(service, 'collect_panel', side_effect=deleted):
            self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': 'sites'}, success=False)
        self.assertFalse(models.PanelInventorySnapshot.objects.exists())
        self.assertIsNotNone(service.acquire_lease(timezone.now()))

    def test_deleted_panel_and_lost_lease_results_are_discarded(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect()
        def lost_lease(*args):
            models.PanelInventoryLease.objects.update(token='successor')
            return rows
        with patch.object(service, 'collect_panel', side_effect=lost_lease):
            self.assertEqual(self.collect(panel)['status'], 'busy')
        self.assertFalse(models.PanelInventorySnapshot.objects.exists())
        self.assertEqual(models.PanelInventoryLease.objects.get().token, 'successor')
        models.PanelInventoryLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        def deleted(*args):
            models.Panel.objects.filter(pk=panel.pk).delete()
            return rows
        with patch.object(service, 'collect_panel', side_effect=deleted):
            self.request('post', '/baota/inventory/collect/', {'panel_id': panel.pk, 'kind': 'sites'}, success=False)
        self.assertFalse(models.PanelInventorySnapshot.objects.exists())

    def test_unknown_fields_nested_in_worker_rows_are_not_stored_or_returned(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect()
        rows[0]['domains'][0]['private_key'] = 'SECRET'
        rows[0]['private_key'] = 'SECRET'
        with patch.object(service, 'collect_panel', return_value=rows):
            self.collect(panel)
        stored = models.PanelInventorySnapshot.objects.get(panel=panel)
        self.assertNotIn('SECRET', json.dumps(stored.rows))
        self.assertNotIn('SECRET', json.dumps(self.request('get', '/baota/inventory/sites/')))

    def test_first_failure_and_resource_limit_never_claim_an_empty_success(self):
        service, panel = self.service(), self.configured()
        readers = importlib.import_module('apps.baota.inventory.readers')
        with patch.object(service, 'collect_panel', side_effect=readers.InventoryError('limit')):
            result = self.collect(panel)
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['sampled_at'])
        self.expire_cooldown()
        with patch.object(service, 'collect_panel', return_value=self.fake_collect()):
            self.collect(panel)
        self.expire_cooldown()
        with patch.object(service, 'collect_panel', side_effect=readers.InventoryError('limit')):
            self.assertEqual(self.collect(panel)['status'], 'stale')
        self.assertEqual(self.request('get', '/baota/inventory/sites/')['total'], 1)

    def test_future_backup_times_have_unknown_age(self):
        service, panel = self.service(), self.configured()
        rows = self.fake_collect('backups')
        rows[0]['backup_at'] = (timezone.now() + timedelta(hours=1)).isoformat()
        with patch.object(service, 'collect_panel', return_value=rows):
            self.collect(panel, 'backups')
        result = self.request('get', '/baota/inventory/backups/')['items'][0]
        self.assertIsNone(result['age_hours'])
        self.assertIsNone(result['overdue'])

    def test_overdue_compares_actual_elapsed_time_before_display_rounding(self):
        service, panel = self.service(), self.configured()
        now = timezone.now()
        rows = self.fake_collect('backups')
        rows[0]['backup_at'] = (now - timedelta(hours=48, seconds=1)).isoformat()
        with patch.object(service, 'collect_panel', return_value=rows):
            self.collect(panel, 'backups')
        with patch.object(service.timezone, 'now', return_value=now):
            result = self.request('get', '/baota/inventory/backups/', {'state': 'overdue'})
        self.assertEqual(result['total'], 1)

    def test_mariadb_lease_concurrent_admission_and_expired_owner_cannot_release_successor(self):
        service = self.service()
        if connection.vendor != 'mysql':
            self.skipTest('Real row concurrency requires MariaDB')
        lease_model = models.PanelInventoryLease
        lease_model.objects.create(slot=1, expires_at=timezone.now() - timedelta(seconds=1))
        barrier, results, errors = threading.Barrier(2), [], []
        def claim():
            close_old_connections()
            try:
                barrier.wait(5)
                results.append(service.acquire_lease(timezone.now()))
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()
        workers = [threading.Thread(target=claim) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(errors, [])
        winners = [result for result in results if result]
        self.assertEqual(len(winners), 1)
        lease_model.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        successor = service.acquire_lease(timezone.now())
        service.release_lease(winners[0])
        self.assertEqual(lease_model.objects.get().token, successor)

    def test_mariadb_cold_start_lease_creation_allows_only_one_process(self):
        service = self.service()
        if connection.vendor != 'mysql':
            self.skipTest('Real row concurrency requires MariaDB')
        barrier, results, errors = threading.Barrier(2), [], []
        def claim():
            close_old_connections()
            try:
                barrier.wait(5)
                results.append(service.acquire_lease(timezone.now()))
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()
        workers = [threading.Thread(target=claim) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len([result for result in results if result]), 1)
        self.assertEqual(models.PanelInventoryLease.objects.count(), 1)
