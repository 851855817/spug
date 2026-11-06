"""Resource access and quota invariants; no customer transports are used."""
from django.core.cache import cache
from django.test import TestCase
from unittest.mock import patch

from apps.account import access
from apps.account.models import User, Role
from apps.account.models import ResourceAccessAudit
from apps.host.models import Host, Group
from apps.baota.models import Panel, PanelGroup
from apps.baota.resource_monitor.models import ResourceMonitorPreference as Preference


class ResourceAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create(username='admin', is_supper=True)
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b')
        self.group = PanelGroup.objects.create(name='panels')
        self.host = Host.objects.create(name='a host', hostname='192.0.2.1', port=22,
                                        username='root', created_by=self.a)
        self.other = Host.objects.create(name='b host', hostname='192.0.2.1', port=22,
                                         username='root', created_by=self.b)
        self.panel = Panel.objects.create(name='a panel', url='http://192.0.2.1:8888', username='test',
                                          password_ciphertext='', group=self.group, created_by=self.a)
        self.other_panel = Panel.objects.create(name='b panel', url='http://192.0.2.1:8888', username='test',
                                                password_ciphertext='', group=self.group, created_by=self.b)

    def test_only_own_resources_are_visible(self):
        self.assertEqual(access.allowed_ids(self.a, 'host'), {self.host.pk})
        self.assertEqual(access.allowed_ids(self.a, 'panel'), {self.panel.pk})
        self.assertEqual(access.allowed_ids(self.admin, 'host'), {self.host.pk, self.other.pk})

    def test_mixed_target_request_is_wholly_rejected(self):
        with self.assertRaisesRegex(ValueError, '权限|无权'):
            access.require_resources(self.a, 'host', [self.host.pk, self.other.pk])

    def test_same_ip_is_not_implicitly_merged(self):
        self.assertEqual(access.quota_usage(self.a)['used'], 2)

    def test_confirmed_link_merges_visible_pair_only(self):
        Preference.objects.create(panel=self.panel, linked_host=self.host)
        self.assertEqual(access.quota_usage(self.a)['used'], 1)

    def test_link_does_not_grant_access_or_leak_other_connection(self):
        Preference.objects.create(panel=self.other_panel, linked_host=self.host)
        self.assertNotIn(self.other_panel.pk, access.allowed_ids(self.a, 'panel'))
        self.assertEqual(access.quota_usage(self.a)['used'], 2)

    def test_visible_panels_linked_to_same_invisible_host_count_once(self):
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'panel', [self.other_panel.pk], 'read')
        Preference.objects.create(panel=self.panel, linked_host=self.other)
        Preference.objects.create(panel=self.other_panel, linked_host=self.other)
        self.assertEqual(access.quota_usage(self.a)['used'], 2)  # own host + one shared server
        self.assertNotIn(self.other.pk, access.allowed_ids(self.a, 'host'))

    def test_read_grant_is_not_manage_grant(self):
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        self.assertIn(self.other.pk, access.allowed_ids(self.a, 'host'))
        self.assertNotIn(self.other.pk, access.allowed_ids(self.a, 'host', manage=True))
        self.assertEqual(access.quota_usage(self.a)['used'], 3)

    def test_grant_can_be_revoked_without_stale_user_object(self):
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'panel', [self.other_panel.pk], 'manage')
        self.assertIn(self.other_panel.pk, access.allowed_ids(self.a, 'panel', manage=True))
        access.assign(self.admin, self.a, 'panel', [self.other_panel.pk], 'none')
        self.assertNotIn(self.other_panel.pk, access.allowed_ids(self.a, 'panel'))

    def test_new_account_limit_is_zero(self):
        self.assertEqual(access.quota_usage(self.b)['limit'], 0)
        with self.assertRaisesRegex(ValueError, '额度|上限|管理'):
            with access.quota_guard([self.b.pk]):
                Host.objects.create(name='new', hostname='192.0.2.4', username='root', created_by=self.b)
        self.assertFalse(Host.objects.filter(name='new').exists())

    def test_quota_can_only_be_changed_by_active_admin(self):
        with self.assertRaises(ValueError):
            access.set_policy(self.a, self.a, None)
        self.admin.is_active = False
        self.admin.save()
        with self.assertRaises(ValueError):
            access.set_policy(self.admin, self.a, 10)

    def test_explicit_unlimited_is_distinct_from_zero(self):
        access.set_policy(self.admin, self.a, None)
        self.assertIsNone(access.quota_usage(self.a)['limit'])
        with access.quota_guard([self.a.pk]):
            Host.objects.create(name='new', hostname='192.0.2.4', username='root', created_by=self.a)
        self.assertEqual(access.quota_usage(self.a)['used'], 3)

    def test_failed_grant_rolls_back_if_no_capacity(self):
        access.set_policy(self.admin, self.a, 2)
        with self.assertRaises(ValueError):
            access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        self.assertNotIn(self.other.pk, access.allowed_ids(self.a, 'host'))

    def test_lowering_quota_preserves_existing_access(self):
        access.set_policy(self.admin, self.a, 1)
        self.assertEqual(access.allowed_ids(self.a, 'host'), {self.host.pk})
        with access.quota_guard([self.a.pk]):
            self.host.name = 'renamed'
            self.host.save()
        with self.assertRaises(ValueError):
            with access.quota_guard([self.a.pk]):
                Host.objects.create(name='new', hostname='192.0.2.4', username='root', created_by=self.a)

    def test_transfer_preserves_creator_and_changes_access(self):
        access.set_policy(self.admin, self.b, 10)
        access.transfer(self.admin, 'host', self.host.pk, self.b)
        self.host.refresh_from_db()
        self.assertEqual(self.host.created_by_id, self.a.pk)
        self.assertNotIn(self.host.pk, access.allowed_ids(self.a, 'host'))
        self.assertIn(self.host.pk, access.allowed_ids(self.b, 'host', manage=True))

    def test_transfer_cannot_overflow_new_owner(self):
        access.set_policy(self.admin, self.b, 2)
        with self.assertRaises(ValueError):
            access.transfer(self.admin, 'host', self.host.pk, self.b)
        self.assertIn(self.host.pk, access.allowed_ids(self.a, 'host'))

    def test_deleted_or_disabled_account_is_denied_even_with_stale_instance(self):
        User.objects.filter(pk=self.a.pk).update(is_deleted=True)
        with self.assertRaises(ValueError):
            access.allowed_ids(self.a, 'host')

    def test_role_change_is_seen_without_cached_permissions(self):
        role = Role.objects.create(name='reader', page_perms={'host': {'host': ['view']}})
        self.a.roles.add(role)
        access.require(self.a, 'host.host.view')
        role.page_perms = {}
        role.save()
        with self.assertRaises(ValueError):
            access.require(self.a, 'host.host.view')

    def test_group_permission_does_not_grant_other_users_assets(self):
        g = Group.objects.create(name='shared')
        g.hosts.add(self.host, self.other)
        role = Role.objects.create(name='legacy', group_perms=[g.pk])
        self.a.roles.add(role)
        self.assertEqual(access.allowed_ids(self.a, 'host'), {self.host.pk})

    def test_group_read_exposes_ancestors_but_no_sibling_ownership(self):
        parent = Group.objects.create(name='parent')
        own = Group.objects.create(name='own', parent_id=parent.pk)
        sibling = Group.objects.create(name='sibling', parent_id=parent.pk)
        own.hosts.add(self.host)
        access.own_group(self.a, 'host', own.pk)
        access.own_group(self.b, 'host', sibling.pk)
        self.assertEqual(access.group_ids(self.a, 'host'), {parent.pk, own.pk})
        self.assertNotIn(parent.pk, access.group_ids(self.a, 'host', manage=True))

    def test_read_grant_does_not_reveal_unassigned_group_labels(self):
        allowed = Group.objects.create(name='allowed')
        hidden = Group.objects.create(name='secret-team')
        self.other.groups.add(allowed, hidden)
        self.a.roles.add(Role.objects.create(name='labels', group_perms=[allowed.pk]))
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        self.assertIn(self.other.pk, access.allowed_ids(self.a, 'host'))
        self.assertEqual(access.group_ids(self.a, 'host'), {allowed.pk})

    def test_invalid_resource_ids_and_grant_level_are_rejected(self):
        for ids in ([True], ['1'], [0], [-1]):
            with self.assertRaises(ValueError):
                access.require_resources(self.a, 'host', ids)
        with self.assertRaises(ValueError):
            access.assign(self.admin, self.a, 'host', [self.other.pk], 'anything')

    def test_quota_denial_audit_survives_rejected_resource_transaction(self):
        with self.assertRaises(ValueError):
            with access.quota_guard([self.a.pk]):
                Host.objects.create(name='rejected', hostname='192.0.2.7', username='root', created_by=self.a)
        self.assertTrue(ResourceAccessAudit.objects.filter(account=self.a, event='quota_denied').exists())
        self.assertFalse(Host.objects.filter(name='rejected').exists())

    def test_permission_denial_writes_only_safe_audit_metadata(self):
        with self.assertRaises(ValueError):
            access.require_resources(self.a, 'host', [self.other.pk], manage=True)
        row = ResourceAccessAudit.objects.filter(actor=self.a, event='resource_denied').get()
        self.assertEqual(row.detail, {'kind': 'host', 'ids': [self.other.pk], 'manage': True})

    def test_locked_permission_check_sees_revocation_before_lock_acquisition(self):
        original = access.current_user
        def revoke_before_lock(user, lock=False):
            if lock:
                User.objects.filter(pk=self.admin.pk).update(is_supper=False)
            return original(user, lock=lock)
        with patch.object(access, 'current_user', side_effect=revoke_before_lock):
            with self.assertRaises(ValueError):
                access.require(self.admin, 'host.host.edit', lock=True)

    def test_host_removal_checks_accounts_using_only_its_linked_panels(self):
        access.set_policy(self.admin, self.a, 10)
        access.assign(self.admin, self.a, 'panel', [self.other_panel.pk], 'read')
        Preference.objects.create(panel=self.panel, linked_host=self.other)
        Preference.objects.create(panel=self.other_panel, linked_host=self.other)
        self.host.delete()
        access.set_policy(self.admin, self.a, 1)
        other_id = self.other.pk
        self.assertIn(self.a.pk, access.affected_accounts('host', [other_id]))
        with self.assertRaises(ValueError):
            with access.quota_guard(access.affected_accounts('host', [other_id]), actor=self.admin):
                self.other.delete()
        self.assertTrue(Host.objects.filter(pk=other_id).exists())
