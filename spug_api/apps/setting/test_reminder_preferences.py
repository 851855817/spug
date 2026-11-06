"""The existing personal settings API must isolate reminder mutes by account."""
import json

from django.test import RequestFactory, TestCase

from apps.account.models import User
from apps.setting.models import UserSetting
from apps.setting.user import UserSettingView
from apps.notify.models import Notify


class PersonalReminderPreferencesTests(TestCase):
    def setUp(self):
        self.first = User.objects.create(username='first', nickname='First')
        self.other = User.objects.create(username='other', nickname='Other')
        self.factory = RequestFactory()

    def request(self, user, body=None):
        request = (self.factory.post('/api/setting/user/', json.dumps(body), content_type='application/json')
                   if body is not None else self.factory.get('/api/setting/user/'))
        request.user = user
        response = UserSettingView.as_view()(request)
        data = json.loads(response.content)
        self.assertFalse(data['error'])
        return data['data']

    def test_mute_and_restore_are_personal_and_do_not_mark_global_notifications_read(self):
        key = 'mute:v1:' + 'a' * 24
        notify = Notify.objects.create(title='Example', source='alert', content='Example', unread=True)
        UserSetting.objects.create(user=self.first, key='terminal', value='{"fontSize":16}')
        muted = json.dumps({'version': 1, 'identity': 'server:host:9', 'cycle': '2030-01-02',
                            'label': 'Example', 'muted': True})
        self.request(self.first, {'key': key, 'value': muted})
        self.assertEqual(self.request(self.first)[key], muted)
        self.assertNotIn(key, self.request(self.other))
        self.assertEqual(self.request(self.first)['terminal'], '{"fontSize":16}')
        restored = json.dumps(dict(json.loads(muted), muted=False))
        self.request(self.first, {'key': key, 'value': restored})
        self.assertNotIn(key, self.request(self.other))
        notify.refresh_from_db()
        self.assertTrue(notify.unread)
        self.assertEqual(UserSetting.objects.filter(key=key).count(), 1)
