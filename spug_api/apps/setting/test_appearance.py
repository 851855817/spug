"""The site appearance is global, public to read, and restricted to save."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections
from django.db.models.query import QuerySet
from django.test import Client, TestCase, TransactionTestCase

from apps.account.models import User
from apps.setting.models import KEYS_DEFAULT, Setting
from apps.setting.utils import AppSetting


PUBLIC_URL = '/setting/appearance/public/'
ADMIN_URL = '/setting/appearance/'
DEFAULT = {'version': 1, 'preset': 'classic-blue', 'revision': 'default'}
PRESETS = ('classic-blue', 'jade', 'indigo', 'graphite', 'night')


def make_user(username, token, **fields):
    return User.objects.create(
        username=username, nickname=username, access_token=token * 32,
        token_expired=int(time.time()) + 3600, last_ip='', **fields)


def save(client, user, preset='jade', revision='default'):
    return client.post(ADMIN_URL, json.dumps({'preset': preset, 'revision': revision}),
                       content_type='application/json', HTTP_X_TOKEN=user.access_token)


class AppearanceTests(TestCase):
    def setUp(self):
        self.admin = make_user('admin', 'a', is_supper=True)
        self.other_admin = make_user('other-admin', 'b', is_supper=True)
        self.employee = make_user('employee', 'c')

    def payload(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content[:250])
        data = response.json()
        self.assertEqual(set(data), {'data', 'error'})
        self.assertFalse(data['error'])
        self.assertEqual(set(data['data']), set(DEFAULT))
        return data['data']

    def test_anonymous_get_returns_only_safe_default_without_creating_a_setting(self):
        Setting.objects.create(key='api_key', value=json.dumps('fixture-private-value'))
        self.assertEqual(self.payload(self.client.get(PUBLIC_URL)), DEFAULT)
        self.assertFalse(Setting.objects.filter(key='ui_appearance').exists())

    def test_public_endpoint_rejects_every_non_get_method_without_writing(self):
        for method in ('post', 'put', 'patch', 'delete', 'head', 'options', 'trace'):
            with self.subTest(method=method):
                response = self.client.generic(method.upper(), PUBLIC_URL, '{}',
                                               content_type='application/json')
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response['Allow'], 'GET')
        self.assertFalse(Setting.objects.filter(key='ui_appearance').exists())

    def test_public_auth_exception_is_exact_and_other_settings_stay_protected(self):
        self.assertEqual(self.client.get(PUBLIC_URL).status_code, 200)
        for path in ('/setting/', ADMIN_URL, PUBLIC_URL + 'extra/', '/setting/appearance/public'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_admin_can_read_and_save_every_template_with_fresh_random_revisions(self):
        current = self.payload(self.client.get(ADMIN_URL, HTTP_X_TOKEN=self.admin.access_token))
        self.assertEqual(current, DEFAULT)
        revisions = set()
        for preset in PRESETS:
            current = self.payload(save(self.client, self.admin, preset, current['revision']))
            self.assertEqual(current['preset'], preset)
            self.assertEqual(current['version'], 1)
            self.assertRegex(current['revision'], r'^[0-9a-f]{32}$')
            self.assertNotIn(current['revision'], revisions)
            revisions.add(current['revision'])
        stored = Setting.objects.get(key='ui_appearance').real_val
        self.assertEqual(stored, current)

    def test_saved_template_is_global_across_accounts_and_anonymous_clients(self):
        saved = self.payload(save(self.client, self.admin))
        for user in (None, self.employee, self.other_admin):
            headers = {'HTTP_X_TOKEN': user.access_token} if user else {}
            self.assertEqual(self.payload(Client().get(PUBLIC_URL, **headers)), saved)
        self.assertEqual(self.payload(self.client.get(ADMIN_URL,
                         HTTP_X_TOKEN=self.other_admin.access_token)), saved)

    def test_employee_cannot_read_or_write_admin_configuration(self):
        for method in ('get', 'post'):
            response = (self.client.get(ADMIN_URL, HTTP_X_TOKEN=self.employee.access_token)
                        if method == 'get' else save(self.client, self.employee))
            self.assertEqual(response.status_code, 403)
            self.assertTrue(response.json()['error'])
        self.assertFalse(Setting.objects.filter(key='ui_appearance').exists())

    def test_inactive_deleted_and_expired_admins_cannot_read_or_save(self):
        cases = ({'is_active': False}, {'is_deleted': True}, {'token_expired': 0})
        for index, fields in enumerate(cases):
            user = make_user('blocked-' + str(index), str(index + 1), is_supper=True)
            User.objects.filter(pk=user.pk).update(**fields)
            for response in (self.client.get(ADMIN_URL, HTTP_X_TOKEN=user.access_token),
                             save(self.client, user)):
                self.assertIn(response.status_code, (401, 403))
                self.assertTrue(response.json()['error'])
        self.assertFalse(Setting.objects.filter(key='ui_appearance').exists())

    def test_invalid_post_bodies_are_rejected_without_modifying_saved_configuration(self):
        saved = self.payload(save(self.client, self.admin))
        invalid = ('{', 'null', '[]', '"jade"', '{}',
                   json.dumps({'preset': 'unknown', 'revision': saved['revision']}),
                   json.dumps({'preset': ['jade'], 'revision': saved['revision']}),
                   json.dumps({'preset': 'jade', 'revision': None}),
                   json.dumps({'preset': 'jade', 'revision': 1}),
                   json.dumps({'preset': 'jade', 'revision': 'x' * 32}),
                   json.dumps({'preset': 'jade', 'revision': 'a' * 33}),
                   json.dumps({'preset': 'jade', 'revision': saved['revision'], 'css': 'body{}'}),
                   json.dumps({'preset': 'jade', 'revision': saved['revision'], 'version': 1}),
                   json.dumps({'preset': 'x' * 4096, 'revision': saved['revision']}))
        for body in invalid:
            with self.subTest(body=body[:90]):
                response = self.client.post(ADMIN_URL, body, content_type='application/json',
                                            HTTP_X_TOKEN=self.admin.access_token)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.json()['error'])
                self.assertEqual(Setting.objects.get(key='ui_appearance').real_val, saved)

    def test_stale_revision_conflicts_and_does_not_overwrite_another_admin(self):
        saved = self.payload(save(self.client, self.admin))
        response = save(self.client, self.other_admin, 'night')
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()['error'])
        self.assertEqual(self.payload(self.client.get(PUBLIC_URL)), saved)

    def test_invalid_stored_values_fall_back_to_default_without_exposing_extra_fields(self):
        invalid = ('not-json', '', 'null', '[]',
                   json.dumps({'version': 2, 'preset': 'jade', 'revision': 'a' * 32}),
                   json.dumps({'version': True, 'preset': 'jade', 'revision': 'a' * 32}),
                   json.dumps({'version': 1, 'preset': 'unknown', 'revision': 'a' * 32}),
                   json.dumps({'version': 1, 'preset': 'jade', 'revision': 'invalid'}),
                   json.dumps({'version': 1, 'preset': 'jade', 'revision': 'default'}))
        for value in invalid:
            Setting.objects.update_or_create(key='ui_appearance', defaults={'value': value})
            with self.subTest(value=value):
                self.assertEqual(self.payload(self.client.get(PUBLIC_URL)), DEFAULT)
        safe = {'version': 1, 'preset': 'jade', 'revision': 'a' * 32}
        Setting.objects.filter(key='ui_appearance').update(
            value=json.dumps(dict(safe, secret='fixture-private-value')))
        self.assertEqual(self.payload(self.client.get(PUBLIC_URL)), safe)

    def test_admin_can_repair_invalid_stored_configuration_using_default_revision(self):
        Setting.objects.create(key='ui_appearance', value='invalid')
        saved = self.payload(save(self.client, self.admin, 'night'))
        self.assertEqual(saved['preset'], 'night')
        self.assertEqual(self.payload(self.client.get(PUBLIC_URL)), saved)

    def test_appearance_is_not_writable_through_the_generic_setting_helper(self):
        self.assertNotIn('ui_appearance', KEYS_DEFAULT)
        with self.assertRaises(KeyError):
            AppSetting.set('ui_appearance', DEFAULT)
        self.assertFalse(Setting.objects.filter(key='ui_appearance').exists())

    def test_saving_appearance_preserves_existing_settings(self):
        existing = Setting.objects.create(key='mail_service', value='{"fixture":"unchanged"}')
        before = existing.value
        self.payload(save(self.client, self.admin))
        existing.refresh_from_db()
        self.assertEqual(existing.value, before)

    def test_database_read_failure_returns_a_safe_retryable_error(self):
        with patch.object(Setting.objects, 'filter', side_effect=DatabaseError('fixture-private-value')):
            response = self.client.get(PUBLIC_URL)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()['error'])
        self.assertNotIn('fixture-private-value', response.content.decode())

    def test_database_save_failure_preserves_the_previous_appearance_and_hides_details(self):
        saved = self.payload(save(self.client, self.admin))
        with patch.object(QuerySet, 'update', side_effect=DatabaseError('fixture-private-value')):
            response = save(self.client, self.admin, 'night', saved['revision'])
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()['error'])
        self.assertNotIn('fixture-private-value', response.content.decode())
        self.assertEqual(self.payload(self.client.get(PUBLIC_URL)), saved)


class AppearanceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = make_user('first-admin', 'a', is_supper=True)
        self.other_admin = make_user('second-admin', 'b', is_supper=True)

    def race(self, revision):
        barrier = threading.Barrier(2, timeout=10)
        first = QuerySet.first

        def simultaneous_read(query):
            value = first(query)
            if query.model is Setting and 'ui_appearance' in str(query.query):
                barrier.wait()
            return value

        def write(user, preset):
            close_old_connections()
            try:
                response = save(Client(), user, preset, revision)
                self.assertIn(response.status_code, (200, 409), response.content[:250])
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with patch.object(QuerySet, 'first', simultaneous_read), ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda args: write(*args),
                                     ((self.admin, 'jade'), (self.other_admin, 'night'))))
        self.assertEqual(sorted(status for status, _ in responses), [200, 409])
        winner = next(data['data'] for status, data in responses if status == 200)
        self.assertEqual(Setting.objects.get(key='ui_appearance').real_val, winner)

    def test_simultaneous_first_writes_have_one_winner(self):
        self.race('default')

    def test_simultaneous_updates_have_one_winner(self):
        Setting.objects.create(key='ui_appearance', value=json.dumps(
            {'version': 1, 'preset': 'classic-blue', 'revision': 'a' * 32}))
        self.race('a' * 32)
