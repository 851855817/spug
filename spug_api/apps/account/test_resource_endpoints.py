"""HTTP boundaries use isolated database rows; no remote actions."""
import json
import time
from django.test import TestCase, override_settings
from django.core.cache import cache
from apps.account.models import User, Role, ResourceAccessAudit
from apps.account import access
from apps.host.models import Host


@override_settings(ROOT_URLCONF='apps.account.resource_test_urls',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher',
                                     'django.contrib.auth.hashers.PBKDF2PasswordHasher'])
class ResourceEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = self.user('admin', True)
        self.a = self.user('a')
        self.b = self.user('b')
        self.host = Host.objects.create(name='A', hostname='192.0.2.1', username='root', created_by=self.a)
        self.other = Host.objects.create(name='B', hostname='192.0.2.2', username='root', created_by=self.b)

    def user(self, name, superuser=False):
        return User.objects.create(username=name, is_supper=superuser, access_token=name.ljust(32, 'x'),
                                    token_expired=int(time.time()) + 3600, last_ip='')

    def request(self, user, method='get', data=None, query=''):
        return getattr(self.client, method)('/resources/' + query, json.dumps(data or {}) if method != 'get' else {},
                                            content_type='application/json', HTTP_X_TOKEN=user.access_token).json()

    def test_self_only_returns_own_quota_permissions_and_resources(self):
        data = self.request(self.a)['data']
        self.assertEqual(data['quota'], {'used': 1, 'limit': 0, 'remaining': 0})
        self.assertEqual([row['id'] for row in data['hosts']], [self.host.pk])
        self.assertNotIn('pkey', str(data))

    def test_effective_resource_rows_distinguish_ownership_from_assignment(self):
        access.set_policy(self.admin, self.a, 3)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'manage')
        rows = self.request(self.a)['data']['hosts']
        self.assertEqual(next(row for row in rows if row['id'] == self.host.pk).get('source'), 'owned')
        self.assertEqual(next(row for row in rows if row['id'] == self.other.pk).get('source'), 'assigned')

    def test_other_account_requires_admin(self):
        self.assertTrue(self.request(self.a, query='?user_id=%s' % self.b.pk)['error'])

    def test_admin_can_set_limit_and_grants(self):
        result = self.request(self.admin, 'post', {'user_id': self.a.pk, 'limit': 2,
                                                  'grants': [{'kind': 'host', 'id': self.other.pk, 'level': 'read'}]})
        self.assertFalse(result['error'], result)
        data = self.request(self.a)['data']
        self.assertEqual(data['quota']['used'], 2)
        self.assertEqual({r['id']: r['level'] for r in data['hosts']}, {self.host.pk: 'manage', self.other.pk: 'read'})

    def test_non_admin_cannot_set_limit_or_assign(self):
        self.assertTrue(self.request(self.a, 'post', {'user_id': self.a.pk, 'limit': None, 'grants': []})['error'])
        self.assertEqual(access.quota_usage(self.a)['limit'], 0)

    def test_failed_grant_replacement_is_atomic_with_quota_change(self):
        result = self.request(self.admin, 'post', {'user_id': self.a.pk, 'limit': 1,
                                                  'grants': [{'kind': 'host', 'id': self.other.pk, 'level': 'manage'}]})
        self.assertTrue(result['error'])
        self.assertEqual(access.quota_usage(self.a)['limit'], 0)
        self.assertNotIn(self.other.pk, access.allowed_ids(self.a, 'host'))

    def test_missing_grants_does_not_clear_existing_assignments(self):
        access.set_policy(self.admin, self.a, 5)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        result = self.request(self.admin, 'post', {'user_id': self.a.pk, 'limit': 3})
        self.assertFalse(result['error'], result)
        self.assertIn(self.other.pk, access.allowed_ids(self.a, 'host'))

    def test_admin_options_exposes_no_connection_credentials(self):
        result = self.request(self.admin, query='?user_id=%s' % self.a.pk)['data']
        self.assertEqual(len(result['available_hosts']), 2)
        self.assertNotIn('private_key', str(result))
        self.assertNotIn('password', str(result))

    def test_grant_cannot_contain_unknown_or_secret_fields(self):
        result = self.request(self.admin, 'post', {'user_id': self.a.pk, 'limit': 3,
            'grants': [{'kind': 'host', 'id': self.other.pk, 'level': 'manage', 'pkey': 'never-echo'}]})
        self.assertTrue(result['error'])
        self.assertNotIn('never-echo', str(result))

    def test_user_creation_saves_explicit_quota(self):
        result = self.client.post('/user/', json.dumps({'username': 'new', 'password': 'Fixture123',
            'nickname': 'new', 'role_ids': [], 'server_limit': 5, 'resource_grants': []}),
            content_type='application/json', HTTP_X_TOKEN=self.admin.access_token).json()
        self.assertFalse(result['error'], result)
        self.assertEqual(access.quota_usage(User.objects.get(username='new'))['limit'], 5)

    def test_user_creation_rolls_back_with_invalid_limit(self):
        result = self.client.post('/user/', json.dumps({'username': 'rejected', 'password': 'Fixture123',
            'nickname': 'new', 'role_ids': [], 'server_limit': True}),
            content_type='application/json', HTTP_X_TOKEN=self.admin.access_token).json()
        self.assertTrue(result['error'])
        self.assertFalse(User.objects.filter(username='rejected').exists())

    def test_lower_limit_with_unchanged_grants_keeps_existing_access(self):
        access.set_policy(self.admin, self.a, 5)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        result = self.request(self.admin, 'post', {'user_id': self.a.pk, 'limit': 1,
            'grants': [{'kind': 'host', 'id': self.other.pk, 'level': 'read'}]})
        self.assertFalse(result['error'], result)
        self.assertEqual(access.quota_usage(self.a)['limit'], 1)
        self.assertIn(self.other.pk, access.allowed_ids(self.a, 'host'))

    def test_revoking_all_grants_is_audited(self):
        access.set_policy(self.admin, self.a, 5)
        access.assign(self.admin, self.a, 'host', [self.other.pk], 'read')
        before = ResourceAccessAudit.objects.count()
        self.assertFalse(self.request(self.admin, 'post', {'user_id': self.a.pk, 'grants': []})['error'])
        self.assertGreater(ResourceAccessAudit.objects.count(), before)
        audit = ResourceAccessAudit.objects.latest('pk')
        self.assertEqual(audit.detail['before'], [{'kind': 'host', 'id': self.other.pk, 'level': 'read'}])

    def test_user_edit_without_role_ids_preserves_existing_roles(self):
        role = Role.objects.create(name='assigned')
        self.a.roles.add(role)
        result = self.client.post('/user/', json.dumps({'id': self.a.pk, 'username': 'a', 'password': '******',
            'nickname': 'A', 'server_limit': 5}), content_type='application/json',
            HTTP_X_TOKEN=self.admin.access_token).json()
        self.assertFalse(result['error'], result)
        self.assertEqual(list(self.a.roles.values_list('pk', flat=True)), [role.pk])
