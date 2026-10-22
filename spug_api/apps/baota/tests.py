import json
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from django.apps import apps
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.account.models import Role, User


class BaotaAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.key_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.key_dir.cleanup)
        key_path = Path(self.key_dir.name) / 'baota.key'
        key_path.write_bytes(Fernet.generate_key())
        self.settings_override = override_settings(BAOTA_KEY_FILE=str(key_path))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.admin = self.make_user('admin', superuser=True)
        self.authenticate(self.admin)

    def make_user(self, name, superuser=False, perms=None):
        user = User.objects.create(
            username=name, nickname=name, is_supper=superuser,
            access_token=(name + 'x' * 32)[:32], token_expired=int(time.time()) + 3600,
            last_ip='', last_login='', password_hash='',
        )
        if perms:
            role = Role.objects.create(name=name, page_perms={'baota': perms})
            user.roles.add(role)
        return user

    def authenticate(self, user):
        self.client.defaults['HTTP_X_TOKEN'] = user.access_token

    def request(self, method, path, data=None, success=True):
        if method in ('get', 'delete') and data:
            path += '?' + urlencode(data)
        response = getattr(self.client, method)(
            path, data=json.dumps(data or {}) if method == 'post' else (data or {}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content[:250])
        payload = response.json()
        if success:
            self.assertFalse(payload['error'], payload)
        else:
            self.assertTrue(payload['error'], payload)
        return payload['data']

    def group(self, name='生产环境', parent_id=None):
        return self.request('post', '/baota/group/', {'name': name, 'parent_id': parent_id})

    def panel(self, **changes):
        group_id = changes.pop('group_id', None)
        if group_id is None:
            group_id = self.group()['id']
        data = dict(name='业务 A', group_id=group_id,
                    url='https://example.com:8888/secure-entry?from=console',
                    username='paneladmin', password='  P@ss-word 测试  ', desc='测试面板')
        data.update(changes)
        return self.request('post', '/baota/', data)

    def test_unauthenticated_request_is_rejected(self):
        self.client.defaults.clear()
        self.assertEqual(self.client.get('/baota/').status_code, 401)

    def test_user_without_permission_is_rejected(self):
        self.authenticate(self.make_user('no-perms'))
        self.request('get', '/baota/', success=False)
        self.request('get', '/baota/group/', success=False)

    def test_create_list_and_password_is_encrypted_and_never_returned(self):
        record = self.panel()
        self.assertTrue(record['has_password'])
        self.assertNotIn('password', record)
        self.assertNotIn('password_ciphertext', record)
        records = self.request('get', '/baota/')
        self.assertEqual(records, [record])
        panel = apps.get_model('baota', 'Panel').objects.get(pk=record['id'])
        self.assertNotIn('P@ss-word', panel.password_ciphertext)
        self.assertEqual(panel.get_password(), '  P@ss-word 测试  ')
        self.assertEqual(record['url'], 'https://example.com:8888/secure-entry?from=console')

    def test_edit_blank_password_keeps_secret_and_updates_fields(self):
        record = self.panel()
        panel_model = apps.get_model('baota', 'Panel')
        original = panel_model.objects.get(pk=record['id']).password_ciphertext
        record.update(name='新名称', password='')
        saved = self.request('post', '/baota/', record)
        self.assertEqual(saved['name'], '新名称')
        self.assertEqual(panel_model.objects.get(pk=record['id']).password_ciphertext, original)

    def test_edit_new_password_replaces_secret(self):
        record = self.panel()
        record['password'] = 'new password '
        self.request('post', '/baota/', record)
        panel = apps.get_model('baota', 'Panel').objects.get(pk=record['id'])
        self.assertEqual(panel.get_password(), 'new password ')

    def test_invalid_addresses_are_rejected(self):
        group = self.group()
        for url in ['javascript:alert(1)', 'ftp://example.com', '//example.com',
                    'https://user:secret@example.com', 'https://example.com:bad',
                    'https://exa mple.com', 'https://example.com\\@evil.com',
                    'https://example.com\n.evil.com']:
            with self.subTest(url=url):
                self.request('post', '/baota/', dict(name='bad', group_id=group['id'],
                             url=url, username='admin', password='secret'), success=False)

    def test_required_fields_and_missing_records_are_rejected(self):
        self.request('post', '/baota/', {}, success=False)
        self.request('post', '/baota/', dict(name='x', group_id=999, url='https://example.com',
                     username='a', password='secret'), success=False)
        record = self.panel()
        record.update(id=999)
        self.request('post', '/baota/', record, success=False)
        self.request('delete', '/baota/', {'id': 999}, success=False)

    def test_add_permission_does_not_allow_editing_existing_records(self):
        record = self.panel()
        self.authenticate(self.make_user('adder', perms={'panel': ['view', 'add']}))
        record.update(name='unauthorized change')
        self.request('post', '/baota/', record, success=False)
        self.request('delete', '/baota/', {'id': record['id']}, success=False)

    def test_edit_permission_does_not_allow_creating_records(self):
        group = self.group()
        self.authenticate(self.make_user('editor', perms={'panel': ['view', 'edit']}))
        self.request('post', '/baota/', dict(name='new', group_id=group['id'],
                     url='https://example.com', username='a', password='secret'), success=False)

    def test_read_only_user_can_list_but_cannot_modify(self):
        record = self.panel()
        self.authenticate(self.make_user('reader', perms={'panel': ['view']}))
        self.assertEqual(len(self.request('get', '/baota/')), 1)
        self.assertEqual(len(self.request('get', '/baota/group/')), 1)
        self.request('post', '/baota/group/', {'name': 'denied'}, success=False)
        self.request('delete', '/baota/', {'id': record['id']}, success=False)

    def test_group_cannot_be_deleted_with_panels_or_children(self):
        root = self.group()
        child = self.group('子分组', root['id'])
        self.request('delete', '/baota/group/', {'id': root['id']}, success=False)
        self.panel(group_id=child['id'])
        self.request('delete', '/baota/group/', {'id': child['id']}, success=False)

    def test_group_tree_rejects_cycles_and_invalid_parent(self):
        root = self.group()
        child = self.group('子分组', root['id'])
        self.request('post', '/baota/group/', dict(id=root['id'], name='root', parent_id=child['id']), success=False)
        self.request('post', '/baota/group/', dict(id=root['id'], name='root', parent_id=root['id']), success=False)
        self.request('post', '/baota/group/', dict(name='bad', parent_id=999), success=False)

    def test_group_rename_and_delete_empty_group(self):
        group = self.group()
        group['name'] = '重命名'
        self.request('post', '/baota/group/', group)
        groups = self.request('get', '/baota/group/')
        self.assertEqual(groups[0]['name'], '重命名')
        self.request('delete', '/baota/group/', {'id': group['id']})
        self.assertEqual(self.request('get', '/baota/group/'), [])

    def test_panel_can_be_deleted(self):
        record = self.panel()
        self.request('delete', '/baota/', {'id': record['id']})
        self.assertEqual(self.request('get', '/baota/'), [])

    def test_missing_encryption_key_fails_without_storing_plaintext(self):
        group = self.group()
        with override_settings(BAOTA_KEY_FILE=str(Path(self.key_dir.name) / 'missing')):
            self.request('post', '/baota/', dict(name='x', group_id=group['id'],
                         url='https://example.com', username='admin', password='secret'), success=False)

    def test_duplicate_group_name_in_same_parent_is_rejected(self):
        self.group()
        self.request('post', '/baota/group/', {'name': '生产环境'}, success=False)
