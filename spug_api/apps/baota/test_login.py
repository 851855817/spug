import hashlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.account.models import User
from apps.baota.models import Panel
from apps.baota import test_connection as fixtures


class PanelLoginTests(TestCase):
    setUp = fixtures.ConnectionAPITests.setUp
    make_user = fixtures.ConnectionAPITests.make_user
    authenticate = fixtures.ConnectionAPITests.authenticate
    request = fixtures.ConnectionAPITests.request
    group = fixtures.ConnectionAPITests.group
    panel = fixtures.ConnectionAPITests.panel
    local_panel = fixtures.ConnectionAPITests.local_panel
    configured = fixtures.ConnectionAPITests.configured
    token = 'Abc_0123' * 8

    def login(self, record):
        return self.client.post('/baota/login/', json.dumps({'id': record['id']}),
                                content_type='application/json')

    def test_signed_http_and_tls_requests_return_only_configured_one_time_url(self):
        for tls in (False, True):
            with self.subTest(tls=tls):
                server = self.local_panel(response={'status': True, 'msg': self.token}, tls=tls)
                record = self.configured(server, group_id=self.group(str(tls))['id'],
                                         **({'tls_fingerprint': server.fingerprint} if tls else {}))
                response = self.login(record)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'data': {'url': server.url + '/login?tmp_token=' + self.token},
                                                   'error': ''})
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response['Referrer-Policy'], 'no-referrer')
                self.assertEqual(len(server.received), 1)
                path, body, _ = server.received[0]
                self.assertEqual(path, '/config?action=get_tmp_token')
                expected = hashlib.md5((body['request_time'][0] + hashlib.md5(
                    b'fixture-api-secret').hexdigest()).encode()).hexdigest()
                self.assertEqual(body['request_token'], [expected])
                self.assertNotIn('password', body)
                self.assertNotIn('username', body)

    def test_requires_both_fresh_login_and_view_permissions(self):
        server = self.local_panel(response={'status': True, 'msg': self.token})
        record = self.configured(server)
        for index, permissions in enumerate((['view'], ['edit'], ['login'], ['view', 'login'])):
            user = self.make_user('role' + str(index), perms={'panel': permissions})
            self.authenticate(user)
            response = self.login(record)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(bool(response.json()['error']), 'login' not in permissions or 'view' not in permissions)
        self.assertEqual(len(server.received), 1)

    def test_cached_permissions_do_not_survive_role_revocation(self):
        record = self.configured()
        user = self.make_user('revoked', perms={'panel': ['view', 'login']})
        user.set_perms_cache({'baota.panel.view', 'baota.panel.login'})
        user.roles.clear()
        self.authenticate(user)
        with patch('apps.baota.client.PanelClient.request') as remote:
            self.assertTrue(self.login(record).json()['error'])
        remote.assert_not_called()

    def test_missing_api_is_explained_and_password_is_never_used(self):
        record = self.panel()
        with patch.object(Panel, 'get_password', side_effect=AssertionError('must not decrypt')):
            response = self.login(record)
        self.assertEqual(response.status_code, 200)
        self.assertIn('API', response.json()['error'])
        self.assertFalse(response.json()['data'])

    def test_rejects_false_status_and_malformed_tokens_without_exposing_response(self):
        values = [(True, None), (True, 'a' * 63), (True, 'a' * 65), (True, '密' * 64),
                  (True, '/' * 64), (True, 'x' * 63 + '\n'), (1, self.token),
                  ('true', self.token), (False, 'remote-secret'), (True, {'url': 'https://evil.test'})]
        server = self.local_panel()
        record = self.configured(server)
        for status, token in values:
            with self.subTest(status=status, token=token):
                server.response = {'status': status, 'msg': token}
                response = self.login(record)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['error'])
                self.assertFalse(response.json()['data'])
                self.assertNotIn('remote-secret', response.content.decode())
                self.assertNotIn(self.token, response.content.decode())

    def test_configuration_and_permission_changes_during_remote_call_reject_token(self):
        record = self.configured()
        user = self.make_user('operator', perms={'panel': ['view', 'login']})
        self.authenticate(user)
        for change in ('config', 'url', 'key', 'permission', 'inactive', 'deleted', 'session'):
            with self.subTest(change=change):
                saved = Panel.objects.get(pk=record['id'])
                original = dict(url=saved.url, api_config_revision=saved.api_config_revision,
                                api_key_ciphertext=saved.api_key_ciphertext)
                def reply(*args, **kwargs):
                    if change == 'config':
                        Panel.objects.filter(pk=saved.pk).update(api_config_revision=saved.api_config_revision + 1)
                    elif change == 'url':
                        Panel.objects.filter(pk=saved.pk).update(url='https://changed.test/entry')
                    elif change == 'key':
                        Panel.objects.filter(pk=saved.pk).update(api_key_ciphertext='changed')
                    elif change == 'permission':
                        user.roles.clear()
                    else:
                        User.objects.filter(pk=user.pk).update(**{
                            'inactive': {'is_active': False}, 'deleted': {'is_deleted': True},
                            'session': {'access_token': 'z' * 32}}[change])
                    return {'status': True, 'msg': self.token}
                roles = list(user.roles.all())
                with patch('apps.baota.client.PanelClient.request', side_effect=reply):
                    response = self.login(record)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['error'])
                self.assertNotIn(self.token, response.content.decode())
                Panel.objects.filter(pk=saved.pk).update(**original)
                User.objects.filter(pk=user.pk).update(is_active=True, is_deleted=False, access_token=user.access_token)
                user.roles.set(roles)
                cache.clear()

    def test_ignores_caller_url_and_preserves_saved_reverse_proxy_prefix(self):
        record = self.configured()
        with patch('apps.baota.client.PanelClient.request', return_value={'status': True, 'msg': self.token}):
            response = self.client.post('/baota/login/', json.dumps({'id': record['id'],
                'url': 'https://evil.test', 'api_key': 'untrusted'}), content_type='application/json')
        self.assertEqual(response.json()['data']['url'], 'https://api.example.com:8888/proxy/login?tmp_token=' + self.token)

    def test_transport_failure_not_retried_and_remote_body_not_reflected(self):
        for index, options in enumerate(({'disconnect': True}, {'status': 302, 'headers': {'Location': 'https://evil.test'}},
                                        {'raw': b'<html>remote-secret</html>'})):
            with self.subTest(options=options):
                server = self.local_panel(**options)
                record = self.configured(server, group_id=self.group(str(index))['id'])
                response = self.login(record)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['error'])
                self.assertNotIn('remote-secret', response.content.decode())
                self.assertEqual(len(server.received), 1)

    def test_post_only_and_unknown_or_invalid_id_rejected(self):
        for method in ('get', 'put', 'delete'):
            self.assertEqual(getattr(self.client, method)('/baota/login/').status_code, 405)
        for value in (True, 0, 'bad', 99999):
            response = self.login({'id': value})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['error'])

