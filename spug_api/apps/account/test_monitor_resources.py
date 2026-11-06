"""Cross-page source isolation using only local fixture data."""
from django.test import TestCase
from apps.account import access
from apps.account.models import User, Role
from apps.host.models import Host
from apps.baota.models import Panel, PanelGroup
from apps.baota.resource_monitor.models import ResourceMonitorPreference as Preference
from apps.baota.resource_monitor import service as monitor
from apps.monitor.ssl import service as ssl
from apps.baota.inventory import service as inventory
from apps.ops.expiry import service as expiry
from apps.ops.expiry import assets
from apps.host.models import Group
from apps.monitor.ssl.models import SSLSource, SSLJob
from apps.monitor.ssl import jobs, renewal
from apps.setting.models import Setting
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
import json


class MonitorResourceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='admin', is_supper=True)
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b')
        self.role = Role.objects.create(name='monitor', page_perms={
            'host': {'host': ['view', 'edit']}, 'baota': {'panel': ['view', 'edit']},
            'monitor': {'monitor': ['view'], 'server': ['view', 'configure', 'collect'],
                        'ssl': ['view', 'configure', 'check']},
            'ops': {'expiry': ['view', 'edit'], 'inventory': ['view', 'collect']}})
        self.a.roles.add(self.role)
        self.host = Host.objects.create(name='own', hostname='192.0.2.1', username='root', created_by=self.a)
        self.other = Host.objects.create(name='hidden', hostname='192.0.2.2', username='root', created_by=self.b)
        group = PanelGroup.objects.create(name='default')
        self.panel = Panel.objects.create(name='ownpanel', url='http://192.0.2.1:8888', username='root',
                                          group=group, created_by=self.a)
        self.other_panel = Panel.objects.create(name='hiddenpanel', url='http://192.0.2.2:8888', username='root',
                                                group=group, created_by=self.b)

    def test_monitor_sources_show_only_owned_hosts_and_panels(self):
        self.assertEqual([r['id'] for r in monitor.sources(self.a, 'host')['sources']], [self.host.pk])
        self.assertEqual([r['id'] for r in monitor.sources(self.a, 'panel')['sources']], [self.panel.pk])

    def test_monitor_cards_do_not_include_other_accounts(self):
        for obj in (self.host, self.other):
            Preference.objects.create(host=obj, enabled=True)
        for obj in (self.panel, self.other_panel):
            Preference.objects.create(panel=obj, enabled=True)
        rows = monitor.cards(self.a)['cards']
        ids = {(s['kind'], s['id']) for row in rows for s in row['sources']}
        self.assertEqual(ids, {('host', self.host.pk), ('panel', self.panel.pk)})

    def test_readonly_grant_cannot_enable_monitor(self):
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        with self.assertRaises(ValueError):
            monitor.settings(self.a, {'kind': 'host', 'id': self.other.pk, 'enabled': True})

    def test_only_admin_can_confirm_same_server_link(self):
        with self.assertRaises(ValueError):
            monitor.link(self.a, {'panel_id': self.panel.pk, 'host_id': self.host.pk})

    def test_ssl_sources_do_not_disclose_other_accounts(self):
        keys = {r['key'] for r in ssl.sources(self.a)['items']}
        self.assertEqual(keys, {'host:%s' % self.host.pk, 'panel:%s' % self.panel.pk})

    def test_ssl_configure_rejects_hidden_source(self):
        with self.assertRaises(ValueError):
            ssl.configure(self.a, {'key': 'host:%s' % self.other.pk, 'revision': '',
                                   'enabled': True, 'reminders': False, 'interval_hours': 6})

    def test_inventory_options_do_not_disclose_other_panels(self):
        rows = inventory.options(self.a)['panels']
        self.assertEqual([r['id'] for r in rows], [self.panel.pk])

    def test_expiry_listing_and_summary_use_account_scope(self):
        data = expiry.listing(self.a, {})
        self.assertEqual(data['summary']['total'], 2)
        self.assertEqual({r['name'] for r in data['items']}, {'own', 'ownpanel'})

    def test_expiry_inventory_does_not_merge_hidden_source_metadata(self):
        Preference.objects.create(panel=self.other_panel, linked_host=self.host)
        rows = assets.inventory(self.a)
        self.assertEqual(rows['host:%s' % self.host.pk]['sources'],
                         [{'kind': 'host', 'id': self.host.pk, 'name': self.host.name}])

    def test_monitor_cannot_borrow_global_ssh_key_for_ordinary_owned_host(self):
        Setting.objects.create(key='private_key', value=json.dumps('global-fixture-key'))
        Preference.objects.create(host=self.host, enabled=True)
        with patch.object(monitor, 'collect_host', return_value={}) as collect:
            monitor.sample(self.a, {'key': 'host:%s' % self.host.pk, 'interval': 30})
        collect.assert_not_called()

    def test_monitor_configuration_is_independent_of_record_edit_permission(self):
        self.role.page_perms = {'host': {'host': ['view']}, 'monitor': {'server': ['configure']}}
        self.role.save()
        self.assertTrue(monitor.settings(self.a, {'kind': 'host', 'id': self.host.pk, 'enabled': True})['enabled'])

    def test_hidden_group_labels_stay_hidden_on_ssl_and_expiry_pages(self):
        public, private = Group.objects.create(name='public'), Group.objects.create(name='secret-team')
        self.host.groups.add(public, private)
        self.role.group_perms = [public.pk]
        self.role.save()
        for data in (ssl.sources(self.a), expiry.listing(self.a, {})):
            self.assertNotIn('secret-team', json.dumps(data))

    def test_expiry_readonly_linked_panel_cannot_be_changed_through_managed_host(self):
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'panel', [self.other_panel.pk], 'read')
        Preference.objects.create(panel=self.other_panel, linked_host=self.host)
        row = assets.inventory(self.a)['host:%s' % self.host.pk]
        with self.assertRaises(ValueError):
            expiry.save(self.a, row['key'], {'version': row['version'], 'manual_expiry': None,
                'owner_id': None, 'followup': 'pending', 'reminder_enabled': False, 'exempt': False, 'note': 'change'})

    def test_expiry_owner_assignment_cannot_reference_another_account(self):
        row = assets.inventory(self.a)['host:%s' % self.host.pk]
        with self.assertRaises(ValueError):
            expiry.save(self.a, row['key'], {'version': row['version'], 'manual_expiry': None,
                'owner_id': self.b.pk, 'followup': 'pending', 'reminder_enabled': False, 'exempt': False, 'note': ''})

    def test_ssl_all_targets_are_authorized_before_the_first_remote_request(self):
        self.host.pkey = 'fixture'
        self.host.save()
        key = 'host:%s' % self.host.pk
        pref = SSLSource.objects.create(pk=key, owner=self.a, enabled=True)
        job = SSLJob.objects.create(owner=self.a, kind='check', state='running',
            source_keys=[key, 'host:%s' % self.other.pk],
            source_versions={key: {'revision': pref.revision, 'digest': ssl.config_digest('host', self.host)}},
            lease_token='fixture', lease_until=timezone.now() + timedelta(minutes=10))
        with patch.object(jobs, 'discover', return_value={'items': [], 'warnings': []}) as collect:
            jobs.run_check(job)
        collect.assert_not_called()

    def test_admin_can_audit_other_accounts_ssl_jobs(self):
        job = SSLJob.objects.create(owner=self.a, kind='check', source_keys=['host:%s' % self.host.pk])
        self.assertEqual(ssl.jobs_listing(self.admin, {})['total'], 1)
        self.assertEqual(ssl.get_job(self.admin, job.pk)['id'], job.pk)

    def test_ssl_job_returns_only_authorized_source_keys_for_action_controls(self):
        key = 'host:%s' % self.host.pk
        job = SSLJob.objects.create(owner=self.a, kind='check', source_keys=[key])
        self.assertEqual(ssl.get_job(self.a, job.pk)['source_keys'], [key])

    def test_readonly_ssl_view_cannot_cancel_a_renewal(self):
        job = SSLJob.objects.create(owner=self.a, kind='renew', state='prepared', source_keys=['host:%s' % self.host.pk])
        with self.assertRaises(ValueError):
            renewal.cancel(self.a, job.pk, {})

    def test_link_changes_record_actor_and_before_after(self):
        from apps.account.models import ResourceAccessAudit
        monitor.link(self.admin, {'panel_id': self.panel.pk, 'host_id': self.host.pk})
        event = ResourceAccessAudit.objects.filter(event='resource_link_changed').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.detail['panel_id'], self.panel.pk)
        self.assertEqual(event.detail['after_host_id'], self.host.pk)

    def test_ssl_empty_target_history_is_not_readable_by_normal_account(self):
        job = SSLJob.objects.create(owner=self.a, kind='check', source_keys=[], summary={'secret': 'old'})
        with self.assertRaises(PermissionError):
            ssl.get_job(self.a, job.pk)

    def test_ssl_enabled_source_without_an_authorized_key_is_reported_without_crashing(self):
        SSLSource.objects.create(pk='host:%s' % self.host.pk, owner=self.a, enabled=True,
                                 config_digest='old-digest', last_success_at=timezone.now())
        rows = ssl.sources(self.a)['items']
        self.assertEqual(next(r for r in rows if r['kind'] == 'host')['status'], 'unconfigured')

    def test_inventory_snapshot_does_not_disclose_unassigned_panel_group(self):
        from apps.baota.inventory.models import PanelInventorySnapshot
        self.panel.group.name = 'secret-panel-team'
        self.panel.group.save()
        self.panel.api_base_url = 'http://192.0.2.1:8888'
        self.panel.api_key_ciphertext = 'fixture'
        self.panel.save()
        PanelInventorySnapshot.objects.create(panel=self.panel, kind='sites', sampled_at=timezone.now(),
            status='ok', config_digest=inventory.config_digest(self.panel), rows=[{
                'site_id': 1, 'name': 'example.com', 'domains': [], 'php_version': '74',
                'site_status': 'running', 'ssl_state': 'unconfigured', 'expires_at': ''}])
        data = inventory.listing(self.a, 'sites', {})
        self.assertNotIn('secret-panel-team', json.dumps(data))
