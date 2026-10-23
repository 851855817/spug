"""Resource monitoring uses fixtures only; no customer host or panel is contacted."""
import copy
import importlib
import json
import threading
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.urls import resolve
from django.utils import timezone

from apps.account.models import Role
from apps.host.models import Host, Group
from apps.setting.models import Setting
from . import models
from . import tests as legacy


METRICS = {'cpu': {'percent': 23, 'cores': 2},
           'memory': {'used_bytes': 1024, 'total_bytes': 4096, 'percent': 25},
           'load': [0.1, 0.2, 0.3], 'network': {'up_bytes_sec': 123, 'down_bytes_sec': 456},
           'disks': [{'path': '/', 'used_bytes': 10, 'total_bytes': 100, 'percent': 10}]}


class ResourceMonitorTests(TransactionTestCase):
    setUp = legacy.BaotaAPITests.setUp
    make_user = legacy.BaotaAPITests.make_user
    authenticate = legacy.BaotaAPITests.authenticate
    request = legacy.BaotaAPITests.request
    group = legacy.BaotaAPITests.group
    panel = legacy.BaotaAPITests.panel

    def service(self):
        self.assertTrue(hasattr(models, 'ResourceMonitorPreference'), 'monitor models missing')
        return importlib.import_module('apps.baota.resource_monitor.service')

    def host(self, name='SSH A', group=None):
        group = group or Group.objects.create(name='Host group')
        host = Host.objects.create(name=name, hostname='192.0.2.10', port=22, username='root',
                                   pkey='fixture-private-key', is_verified=True, created_by=self.admin)
        host.groups.add(group)
        return host

    def configured_panel(self, **changes):
        return models.Panel.objects.get(pk=self.panel(
            api_base_url='https://api.example.com', api_key='fixture-api-key', **changes)['id'])

    def user(self, name='reader', permissions=(), groups=()):
        user = self.make_user(name)
        page = {}
        for code in permissions:
            module, resource, action = code.split('.')
            page.setdefault(module, {}).setdefault(resource, []).append(action)
        user.roles.add(Role.objects.create(name=name, page_perms=page, group_perms=list(groups)))
        cache.clear()
        return user

    def enable(self, source, kind='panel', enabled=True):
        return self.request('post', '/baota/monitor/settings/',
                            {'kind': kind, 'id': source.pk, 'enabled': enabled})

    def sample(self, key, interval=30):
        return self.request('post', '/baota/monitor/sample/', {'key': key, 'interval': interval})

    def cards(self):
        return self.request('get', '/baota/monitor/cards/')['cards']

    def collectors(self, **kwargs):
        service = self.service()
        self.addCleanup(patch.stopall)
        panel = patch.object(service, 'collect_panel', return_value=copy.deepcopy(METRICS), **kwargs).start()
        host = patch.object(service, 'collect_host', return_value=copy.deepcopy(METRICS)).start()
        return panel, host

    def test_models_have_disabled_default_and_exactly_one_source_constraint(self):
        self.service()
        host = self.host()
        pref = models.ResourceMonitorPreference.objects.create(host=host)
        self.assertFalse(pref.enabled)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.ResourceMonitorPreference.objects.create()
        panel = self.configured_panel()
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.ResourceMonitorPreference.objects.create(host=host, panel=panel)
        self.assertEqual({models.ResourceMonitorPreference._meta.db_table,
                          models.ResourceMonitorSnapshot._meta.db_table,
                          models.ResourceMonitorLease._meta.db_table},
                         {'baota_monitor_preferences', 'baota_monitor_snapshots', 'baota_monitor_leases'})

    def test_sources_default_off_and_never_return_credentials(self):
        host, panel = self.host(), self.configured_panel()
        for kind, source in [('host', host), ('panel', panel)]:
            data = self.request('get', '/baota/monitor/sources/', {'kind': kind})
            self.assertEqual(len(data['sources']), 1)
            self.assertFalse(data['sources'][0]['enabled'])
            self.assertEqual(data['sources'][0]['id'], source.pk)
            self.assertEqual(data['sources'][0]['kind'], kind)
            serialized = json.dumps(data)
            for secret in ('fixture-private-key', 'fixture-api-key', 'ciphertext', 'pkey', 'username'):
                self.assertNotIn(secret, serialized)
        self.assertEqual(self.cards(), [])

    def test_settings_requires_strict_boolean_and_never_collects(self):
        panel = self.configured_panel()
        api, ssh = self.collectors()
        for value in ('false', 0, 1, None, [], {}):
            self.request('post', '/baota/monitor/settings/',
                         {'kind': 'panel', 'id': panel.pk, 'enabled': value}, success=False)
        result = self.enable(panel)
        self.assertTrue(result['enabled'])
        api.assert_not_called()
        ssh.assert_not_called()
        self.assertTrue(self.request('get', '/baota/monitor/sources/', {'kind': 'panel'})['sources'][0]['enabled'])

    def test_settings_requires_both_source_view_and_edit(self):
        panel = self.configured_panel()
        for i, permissions in enumerate([('baota.panel.view',), ('baota.panel.edit',), ()]):
            self.authenticate(self.user('u' + str(i), permissions))
            self.request('post', '/baota/monitor/settings/',
                         {'kind': 'panel', 'id': panel.pk, 'enabled': True}, success=False)
        self.authenticate(self.user('editor', ('baota.panel.view', 'baota.panel.edit')))
        self.assertTrue(self.enable(panel)['enabled'])

    def test_host_groups_filter_sources_settings_cards_and_link_candidates(self):
        visible_group = Group.objects.create(name='allowed')
        host, hidden = self.host(group=visible_group), self.host('hidden')
        self.enable(host, 'host')
        self.enable(hidden, 'host')
        user = self.user(permissions=('host.host.view', 'host.host.edit', 'monitor.monitor.view'),
                         groups=(visible_group.pk,))
        self.authenticate(user)
        data = self.request('get', '/baota/monitor/sources/', {'kind': 'host'})
        self.assertEqual([s['id'] for s in data['sources']], [host.pk])
        self.assertEqual([s['id'] for s in data['hosts']], [host.pk])
        self.assertEqual([c['key'] for c in self.cards()], ['host:' + str(host.pk)])
        self.request('post', '/baota/monitor/settings/',
                     {'kind': 'host', 'id': hidden.pk, 'enabled': True}, success=False)
        self.request('post', '/baota/monitor/sample/',
                     {'key': 'host:' + str(hidden.pk), 'interval': 30}, success=False)

    def test_cards_and_sample_require_monitor_and_source_view(self):
        panel = self.configured_panel()
        self.enable(panel)
        self.authenticate(self.user('only-source', ('baota.panel.view',)))
        self.request('get', '/baota/monitor/cards/', success=False)
        self.request('post', '/baota/monitor/sample/', {'key': 'panel:' + str(panel.pk), 'interval': 30}, success=False)
        self.authenticate(self.user('only-monitor', ('monitor.monitor.view',)))
        self.assertEqual(self.cards(), [])
        self.request('post', '/baota/monitor/sample/', {'key': 'panel:' + str(panel.pk), 'interval': 30}, success=False)
        self.request('get', '/baota/monitor/sources/', {'kind': 'panel'}, success=False)

    def test_same_address_requires_explicit_link_and_only_enabled_sources_merge(self):
        host, panel = self.host(), self.configured_panel(url='https://192.0.2.10:8888/entry')
        self.enable(host, 'host')
        self.enable(panel)
        self.assertEqual(len(self.cards()), 2)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        card = self.cards()[0]
        self.assertEqual(len(self.cards()), 1)
        self.assertEqual(card['key'], 'host:' + str(host.pk))
        self.assertEqual(len(card['sources']), 2)
        self.enable(host, 'host', False)
        self.assertEqual(self.cards()[0]['key'], 'panel:' + str(panel.pk))
        self.assertEqual(self.cards()[0]['sources'], [{'kind': 'panel', 'id': panel.pk, 'name': panel.name}])
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': None})
        self.assertIsNone(self.cards()[0]['linked_host_id'])

    def test_link_requires_both_source_edit_permissions_and_authorized_host(self):
        host, panel = self.host(), self.configured_panel()
        self.authenticate(self.user('panel-editor', ('baota.panel.view', 'baota.panel.edit')))
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk}, success=False)
        self.authenticate(self.user('no-group', ('baota.panel.view', 'baota.panel.edit', 'host.host.view', 'host.host.edit')))
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk}, success=False)

    def test_hidden_panel_does_not_appear_in_merged_card_or_snapshot(self):
        host, panel = self.host(), self.configured_panel()
        self.enable(host, 'host')
        self.enable(panel)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        api, ssh = self.collectors()
        self.sample('host:' + str(host.pk))
        self.authenticate(self.user('host-reader', ('host.host.view', 'monitor.monitor.view'),
                                    groups=(host.groups.first().pk,)))
        card = self.cards()[0]
        self.assertEqual(card['sources'], [{'kind': 'host', 'id': host.pk, 'name': host.name}])
        self.assertIsNone(card['snapshot'])
        self.assertIsNone(card['panel_id'])
        self.assertGreater(self.sample(card['key'])['retry_after'], 0)
        ssh.assert_not_called()
        api.assert_called_once()

    def test_different_visible_source_sets_share_minimum_thirty_second_interval(self):
        host, panel = self.host(), self.configured_panel()
        self.enable(host, 'host')
        self.enable(panel)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        api, ssh = self.collectors()
        now = timezone.now()
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=now):
            self.sample('host:' + str(host.pk))
        self.authenticate(self.user('host-only', ('host.host.view', 'monitor.monitor.view'),
                                    groups=(host.groups.first().pk,)))
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=now + timedelta(seconds=10)):
            result = self.sample('host:' + str(host.pk))
            self.assertIsNone(result['snapshot'])
            self.assertEqual(result['retry_after'], 20)
        ssh.assert_not_called()
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=now + timedelta(seconds=31)):
            self.sample('host:' + str(host.pk))
        ssh.assert_called_once()
        api.assert_called_once()

    def test_api_first_and_only_enabled_ssh_fallback(self):
        host, panel = self.host(), self.configured_panel()
        self.enable(panel)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        api, ssh = self.collectors()
        api.side_effect = RuntimeError('raw credential')
        result = self.sample('panel:' + str(panel.pk))
        self.assertEqual(result['snapshot']['status'], 'error')
        ssh.assert_not_called()
        self.enable(host, 'host')
        result = self.sample('host:' + str(host.pk))
        self.assertEqual(result['snapshot']['source_kind'], 'host')
        self.assertEqual(result['snapshot']['status'], 'ok')
        ssh.assert_called_once()

    def test_disabled_sources_reject_direct_sampling_and_clear_cache(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        self.sample('panel:' + str(panel.pk))
        self.assertEqual(models.ResourceMonitorSnapshot.objects.count(), 1)
        self.enable(panel, enabled=False)
        self.assertEqual(models.ResourceMonitorSnapshot.objects.count(), 0)
        self.assertEqual(self.cards(), [])
        self.request('post', '/baota/monitor/sample/', {'key': 'panel:' + str(panel.pk), 'interval': 30}, success=False)
        api.assert_called_once()

    def test_shared_cache_respects_minimum_interval_and_requested_age(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        start = timezone.now()
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=start):
            first = self.sample('panel:' + str(panel.pk))
        self.authenticate(self.user('readonly', ('baota.panel.view', 'monitor.monitor.view')))
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=start + timedelta(seconds=29)):
            self.assertEqual(self.sample(first['key'])['snapshot'], first['snapshot'])
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=start + timedelta(seconds=31)):
            self.sample(first['key'], 300)
        api.assert_called_once()
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=start + timedelta(seconds=32)):
            self.sample(first['key'], 30)
        self.assertEqual(api.call_count, 2)
        for value in (True, '30', 0, 31, None):
            self.request('post', '/baota/monitor/sample/', {'key': first['key'], 'interval': value}, success=False)

    def test_retry_reason_distinguishes_success_cache_busy_failure_and_shared_backoff(self):
        host, panel = self.host(), self.configured_panel()
        self.enable(host, 'host')
        self.enable(panel)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        api, ssh = self.collectors()
        service, start, key = self.service(), timezone.now(), 'host:' + str(host.pk)
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=start):
            collected = self.sample(key, 300)
        self.assertEqual((collected.get('retry_reason'), collected['retry_after']), ('collected', 300))
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=start + timedelta(seconds=10)):
            cached = self.sample(key, 300)
        self.assertEqual((cached.get('retry_reason'), cached['retry_after']), ('cache', 290))
        later = start + timedelta(seconds=31)
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=later):
            lease = service.acquire_lease(key, later)
            busy = self.sample(key, 30)
            self.assertEqual(busy.get('retry_reason'), 'busy')
            self.assertEqual(busy['snapshot']['status'], 'ok')
            service.release_lease(*lease)
            api.side_effect, ssh.side_effect = RuntimeError('fixture API'), RuntimeError('fixture SSH')
            failed = self.sample(key, 30)
        self.assertEqual((failed.get('retry_reason'), failed['retry_after']), ('backoff', 60))
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=start + timedelta(seconds=40)):
            self.assertEqual(self.sample(key, 30).get('retry_reason'), 'backoff')
        self.authenticate(self.user('only-ssh-reason', ('host.host.view', 'monitor.monitor.view'),
                                    groups=(host.groups.first().pk,)))
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=start + timedelta(seconds=40)):
            isolated = self.sample(key, 30)
        self.assertEqual(isolated.get('retry_reason'), 'backoff')
        self.assertIsNone(isolated['snapshot'])

    def test_config_change_discard_has_busy_retry_reason(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        def changed(_):
            models.Panel.objects.filter(pk=panel.pk).update(api_config_revision=999)
            return METRICS
        api.side_effect = changed
        discarded = self.sample('panel:' + str(panel.pk))
        self.assertIsNone(discarded['snapshot'])
        self.assertEqual(discarded.get('retry_reason'), 'busy')

    def test_credential_changes_invalidate_read_and_inflight_write(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        key = 'panel:' + str(panel.pk)
        self.sample(key)
        models.Panel.objects.filter(pk=panel.pk).update(api_key_ciphertext='new ciphertext')
        self.assertIsNone(self.cards()[0]['snapshot'])
        def changed(_):
            models.Panel.objects.filter(pk=panel.pk).update(api_config_revision=99)
            return METRICS
        api.side_effect = changed
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=timezone.now() + timedelta(seconds=31)):
            self.assertIsNone(self.sample(key)['snapshot'])
        self.assertEqual(api.call_count, 2)
        self.assertIsNone(self.cards()[0]['snapshot'])

    def test_disable_during_collection_discards_result_and_releases_lease(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        api.side_effect = lambda _: (self.enable(panel, enabled=False), METRICS)[1]
        self.assertIsNone(self.sample('panel:' + str(panel.pk))['snapshot'])
        self.assertEqual(models.ResourceMonitorSnapshot.objects.count(), 0)
        self.assertFalse(models.ResourceMonitorLease.objects.exclude(token='').exists())

    def test_failures_use_shared_backoff_and_do_not_store_exception_or_fake_success_time(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        start = timezone.now()
        key = 'panel:' + str(panel.pk)
        with patch('apps.baota.resource_monitor.service.timezone.now', return_value=start):
            good = self.sample(key)['snapshot']
        api.side_effect = RuntimeError('raw-secret body access_token=bad')
        elapsed = 30
        for failure, delay in enumerate((60, 120, 240, 300), start=1):
            now = start + timedelta(seconds=elapsed)
            with patch('apps.baota.resource_monitor.service.timezone.now', return_value=now):
                result = self.sample(key)
                self.assertEqual(result['retry_after'], delay)
                self.assertEqual(result['snapshot']['status'], 'stale')
                self.assertEqual(result['snapshot']['sampled_at'], good['sampled_at'])
                self.sample(key)
            self.assertEqual(api.call_count, failure + 1)
            elapsed += delay
        saved = json.dumps(list(models.ResourceMonitorSnapshot.objects.values()), default=str)
        self.assertNotIn('raw-secret', saved)
        self.assertNotIn('access_token', saved)

    def test_failure_count_is_shared_across_source_visibility_and_success_resets_it(self):
        host, panel = self.host(), self.configured_panel()
        self.enable(host, 'host')
        self.enable(panel)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        reader = self.user('host-only-backoff', ('host.host.view', 'monitor.monitor.view'),
                           groups=(host.groups.first().pk,))
        api, ssh = self.collectors()
        api.side_effect = RuntimeError('fixture API failure')
        ssh.side_effect = RuntimeError('fixture SSH failure')
        start, key = timezone.now(), 'host:' + str(host.pk)
        for user, elapsed, delay in ((self.admin, 0, 60), (reader, 60, 120), (self.admin, 180, 240)):
            self.authenticate(user)
            with patch('apps.baota.resource_monitor.service.timezone.now',
                       return_value=start + timedelta(seconds=elapsed)):
                result = self.sample(key)
            self.assertEqual(result['retry_after'], delay)
            self.assertEqual(result['snapshot']['status'], 'error')
            self.assertEqual(models.ResourceMonitorSnapshot.objects.order_by('-attempted_at').first().failures,
                             (60, 120, 240).index(delay) + 1)
        self.authenticate(reader)
        ssh.side_effect = None
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=start + timedelta(seconds=420)):
            success = self.sample(key)
        self.assertEqual(success['snapshot']['status'], 'ok')
        self.authenticate(self.admin)
        ssh.side_effect = RuntimeError('fixture SSH failure')
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=start + timedelta(seconds=450)):
            reset = self.sample(key)
        self.assertEqual(reset['retry_after'], 60)
        self.assertEqual(reset['snapshot']['status'], 'error')
        self.assertIsNone(reset['snapshot']['sampled_at'])
        self.assertEqual(models.ResourceMonitorSnapshot.objects.order_by('-attempted_at').first().failures, 1)

    def test_whitelisted_metrics_drop_remote_fields_and_invalid_numbers(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        raw = copy.deepcopy(METRICS)
        raw.update(secret='remote body')
        raw['cpu'].update(percent=True, cores=float('nan'), token='remote body')
        raw['memory']['used_bytes'] = -1
        raw['load'] = [float('inf'), True, 0.3]
        raw['disks'] *= 40
        api.return_value = raw
        metrics = self.sample('panel:' + str(panel.pk))['snapshot']['metrics']
        self.assertNotIn('remote body', json.dumps(metrics))
        self.assertIsNone(metrics['cpu']['percent'])
        self.assertIsNone(metrics['cpu']['cores'])
        self.assertIsNone(metrics['memory']['used_bytes'])
        self.assertEqual(metrics['load'], [None, None, 0.3])
        self.assertEqual(len(metrics['disks']), 32)

    def test_two_global_slots_same_target_lock_expiry_and_token_release(self):
        service = self.service()
        now = timezone.now()
        first = service.acquire_lease('host:1', now)
        second = service.acquire_lease('host:2', now)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(service.acquire_lease('host:1', now))
        self.assertIsNone(service.acquire_lease('host:3', now))
        service.release_lease(first[0], 'wrong-token')
        self.assertIsNone(service.acquire_lease('host:3', now))
        replacement = service.acquire_lease('host:1', now + timedelta(seconds=61))
        self.assertIsNotNone(replacement)
        service.release_lease(*first)
        self.assertTrue(models.ResourceMonitorLease.objects.filter(pk=replacement[0], token=replacement[1]).exists())
        self.assertEqual(models.ResourceMonitorLease.objects.count(), 2)

    def test_busy_lease_returns_existing_snapshot_without_network(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        service = self.service()
        service.acquire_lease('panel:' + str(panel.pk), timezone.now())
        result = self.sample('panel:' + str(panel.pk))
        self.assertIsNone(result['snapshot'])
        self.assertGreater(result['retry_after'], 0)
        api.assert_not_called()

    def test_sample_is_non_atomic_with_atomic_requests_enabled(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        api.side_effect = lambda _: self.assertFalse(connection.in_atomic_block) or METRICS
        callback = resolve('/baota/monitor/sample/').func
        self.assertIn('default', getattr(callback, '_non_atomic_requests', set()))
        previous = connection.settings_dict.get('ATOMIC_REQUESTS', False)
        connection.settings_dict['ATOMIC_REQUESTS'] = True
        try:
            self.sample('panel:' + str(panel.pk))
        finally:
            connection.settings_dict['ATOMIC_REQUESTS'] = previous

    def test_malformed_source_kind_is_a_validation_error(self):
        service = self.service()
        for kind in ([], {}, None, True, 'bad'):
            with self.assertRaisesRegex(ValueError, '有效来源'):
                service.settings(self.admin, {'kind': kind, 'id': 1, 'enabled': True})
            self.request('post', '/baota/monitor/settings/',
                         {'kind': kind, 'id': 1, 'enabled': True}, success=False)

    def test_extreme_integer_is_unknown_instead_of_overflowing(self):
        service = self.service()
        result = service.clean_metrics({'cpu': {'cores': 10 ** 1000, 'percent': -1}})
        self.assertEqual(result['cpu'], {'cores': None, 'percent': None})

    def test_host_group_labels_do_not_reveal_unassigned_groups(self):
        allowed, hidden = Group.objects.create(name='allowed'), Group.objects.create(name='secret-team')
        host = self.host(group=allowed)
        host.groups.add(hidden)
        self.enable(host, 'host')
        self.authenticate(self.user(permissions=('host.host.view', 'host.host.edit', 'monitor.monitor.view'),
                                    groups=(allowed.pk,)))
        sources = self.request('get', '/baota/monitor/sources/', {'kind': 'host'})
        self.assertNotIn('secret-team', json.dumps(sources))
        self.assertNotIn('secret-team', json.dumps(self.cards()))

    def test_global_ssh_key_rotation_invalidates_cache_and_pins_current_key(self):
        host = self.host()
        Host.objects.filter(pk=host.pk).update(pkey=None)
        Setting.objects.create(key='private_key', value=json.dumps('global-key-one'))
        self.enable(host, 'host')
        _, ssh = self.collectors()
        self.sample('host:' + str(host.pk))
        self.assertEqual(ssh.call_args.args[0].pkey, 'global-key-one')
        Setting.objects.filter(key='private_key').update(value=json.dumps('global-key-two'))
        self.assertIsNone(self.cards()[0]['snapshot'])
        with patch('apps.baota.resource_monitor.service.timezone.now',
                   return_value=timezone.now() + timedelta(seconds=31)):
            self.sample('host:' + str(host.pk))
        self.assertEqual(ssh.call_args.args[0].pkey, 'global-key-two')

    def test_linked_panel_and_host_visibility_share_internal_lease(self):
        host, panel = self.host(), self.configured_panel()
        self.enable(host, 'host')
        self.enable(panel)
        self.request('post', '/baota/monitor/link/', {'panel_id': panel.pk, 'host_id': host.pk})
        self.service().acquire_lease('host:' + str(host.pk), timezone.now())
        self.authenticate(self.user(permissions=('baota.panel.view', 'monitor.monitor.view')))
        api, _ = self.collectors()
        card = self.cards()[0]
        self.assertEqual(card['key'], 'panel:' + str(panel.pk))
        self.assertIsNone(card['linked_host_id'])
        self.assertIsNone(self.sample(card['key'])['snapshot'])
        api.assert_not_called()

    def test_expired_inflight_lease_cannot_publish_or_release_replacement(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        def replaced(_):
            lease = models.ResourceMonitorLease.objects.exclude(token='').get()
            models.ResourceMonitorLease.objects.filter(pk=lease.pk).update(token='replacement-token')
            return METRICS
        api.side_effect = replaced
        self.assertIsNone(self.sample('panel:' + str(panel.pk))['snapshot'])
        self.assertEqual(models.ResourceMonitorSnapshot.objects.count(), 0)
        self.assertTrue(models.ResourceMonitorLease.objects.filter(token='replacement-token').exists())

    def test_reentrant_sample_during_network_does_not_collect_twice(self):
        panel = self.configured_panel()
        self.enable(panel)
        api, _ = self.collectors()
        def reentered(_):
            busy = self.sample('panel:' + str(panel.pk))
            self.assertIsNone(busy['snapshot'])
            return METRICS
        api.side_effect = reentered
        self.sample('panel:' + str(panel.pk))
        api.assert_called_once()

    def test_mariadb_parallel_claims_cap_global_and_same_target(self):
        if connection.vendor != 'mysql':
            self.skipTest('MariaDB integration proves row locking; SQLite covers admission state transitions')
        service = self.service()
        # Pre-seed durable slots so this test exercises admission, not schema creation.
        service.acquire_lease('seed', timezone.now())
        for lease in models.ResourceMonitorLease.objects.all():
            service.release_lease(lease.pk, lease.token)
        for keys, expected in [(['host:1'] * 4, 1), (['host:1', 'host:2', 'host:3', 'host:4'], 2)]:
            barrier, results, errors = threading.Barrier(4), [], []
            def claim(key):
                close_old_connections()
                try:
                    barrier.wait(timeout=10)
                    results.append(service.acquire_lease(key, timezone.now()))
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    close_old_connections()
            threads = [threading.Thread(target=claim, args=(key,)) for key in keys]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertFalse(any(t.is_alive() for t in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sum(value is not None for value in results), expected)
            for lease in models.ResourceMonitorLease.objects.all():
                service.release_lease(lease.pk, lease.token)
