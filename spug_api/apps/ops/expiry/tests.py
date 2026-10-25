import importlib
import json
import time
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import Mock, patch

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase, SimpleTestCase, TransactionTestCase, skipUnlessDBFeature
from django.db import close_old_connections, connections, OperationalError
from concurrent.futures import ThreadPoolExecutor
import threading

from apps.account.models import User
from apps.host.models import Host, HostExtend, Group
from apps.baota.models import Panel, PanelGroup, ResourceMonitorPreference


class ExpiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = self.user('admin', True)
        self.client.defaults['HTTP_X_TOKEN'] = self.admin.access_token
        self.host = Host.objects.create(name='生产主机', hostname='192.0.2.1', username='SECRET_ACCOUNT',
                                        port=22, pkey='SECRET_KEY', created_by=self.admin)
        self.group = PanelGroup.objects.create(name='面板组')
        self.panel = self.make_panel('生产面板')

    def user(self, name, admin=False):
        return User.objects.create(username=name, nickname=name, is_supper=admin,
                                   access_token=(name + 'x' * 32)[:32], token_expired=int(time.time()) + 3600,
                                   last_ip='', last_login='', password_hash='')

    def make_panel(self, name):
        return Panel.objects.create(name=name, url='https://SECRET_USER:SECRET_PASS@192.0.2.1:8888/SECRET_PATH?key=SECRET',
                                    username='SECRET_PANEL_ACCOUNT', password_ciphertext='SECRET_CIPHER',
                                    group=self.group, created_by=self.admin)

    def original(self, value):
        return HostExtend.objects.update_or_create(host=self.host, defaults={
            'expired_time': value, 'cpu': 1, 'memory': 1, 'os_name': '', 'os_type': '',
            'private_ip_address': '[]', 'public_ip_address': '[]',
            'instance_charge_type': 'Other', 'internet_charge_type': 'Other'})[0]

    def request(self, method, suffix, data=None, error=False):
        response = getattr(self.client, method)('/ops/expiry/' + suffix,
                    data=json.dumps(data or {}) if method == 'post' else data or {}, content_type='application/json')
        if error and response.status_code == 401:
            self.assertTrue(response.json()['error'])
            return response.json()['data']
        self.assertEqual(response.status_code, 200, response.content[:300])
        payload = response.json()
        self.assertEqual(bool(payload['error']), error, payload)
        return payload['data']

    def rows(self, **params):
        return self.request('get', 'assets/', params)

    def row(self, key=None):
        key = key or 'host:%s' % self.host.pk
        return next(row for row in self.rows()['items'] if row['key'] == key)

    def save(self, row=None, **changes):
        row = row or self.row()
        data = {field: row[field] for field in ('version', 'manual_expiry', 'followup', 'reminder_enabled', 'exempt', 'note')}
        data['owner_id'] = row['owner']['id'] if row['owner'] else None
        data.update(changes)
        return self.request('post', 'assets/%s/' % row['key'], data)

    def checker(self):
        return importlib.import_module('apps.ops.expiry.reminders')

    def test_default_union_is_read_only_and_never_exposes_credentials(self):
        with patch.object(Host, 'get_ssh', side_effect=AssertionError('network')):
            data = self.rows()
            options = self.request('get', 'options/')
        self.assertEqual(data['total'], 2)
        self.assertTrue(all(not row['reminder_enabled'] and row['state'] == 'unset' for row in data['items']))
        self.assertNotIn('SECRET', json.dumps(data))
        self.assertEqual(options['timezone'], 'Asia/Shanghai')
        self.assertEqual(options['schedule'], '09:00')
        self.assertEqual(options['check']['status'], 'never')
        self.assertEqual(apps.get_model('ops', 'ExpiryMetadata').objects.count(), 0)

    def test_explicit_links_merge_multiple_panels_even_with_monitoring_disabled(self):
        other = self.make_panel('同机面板')
        for panel in (self.panel, other):
            ResourceMonitorPreference.objects.create(panel=panel, enabled=False, linked_host=self.host)
        result = self.rows()
        self.assertEqual(result['total'], 1)
        self.assertEqual({x['kind'] for x in result['items'][0]['sources']}, {'host', 'panel'})
        self.assertEqual(len(result['items'][0]['sources']), 3)
        self.assertEqual(self.rows(source='panel')['total'], 1)
        self.assertEqual(self.rows(source='host')['total'], 1)
        self.assertFalse(ResourceMonitorPreference.objects.filter(enabled=True).exists())

    def test_original_invalid_missing_and_manual_override_survive_cloud_change(self):
        self.original('2026-02-30')
        self.assertEqual(self.row()['state'], 'invalid')
        row = self.save(manual_expiry='2030-11-10')
        self.assertEqual(row['date_source'], 'manual')
        self.original('2031-01-01 09:30:00')
        row = self.row()
        self.assertEqual(row['expires_on'], '2030-11-10')
        self.assertEqual(row['original_expiry'], '2031-01-01 09:30:00')
        row = self.save(row, manual_expiry=None)
        self.assertEqual(row['expires_on'], '2031-01-01')
        self.assertEqual(row['date_source'], 'host_record')
        self.assertEqual(self.save(row, exempt=True)['state'], 'exempt')

    def test_panel_metadata_pauses_on_link_and_revives_after_unlink(self):
        panel_key = 'panel:%s' % self.panel.pk
        saved = self.save(self.row(panel_key), manual_expiry='2030-12-01', reminder_enabled=True)
        preference = ResourceMonitorPreference.objects.create(panel=self.panel, linked_host=self.host)
        self.assertEqual(self.rows()['total'], 1)
        self.assertIsNone(self.row()['expires_on'])
        self.request('post', 'assets/%s/' % panel_key, dict(version=saved['version']), error=True)
        preference.linked_host = None
        preference.revision = 'unlinked'
        preference.save()
        revived = self.row(panel_key)
        self.assertEqual(revived['expires_on'], '2030-12-01')
        self.assertTrue(revived['reminder_enabled'])
        self.assertNotEqual(revived['version'], saved['version'])

    def test_stale_version_covers_metadata_original_and_association_revision(self):
        row = self.row()
        self.save(row, note='first')
        self.assert_stale(row)
        row = self.row()
        self.original('2030-11-10')
        self.assert_stale(row)
        row = self.row()
        pref = ResourceMonitorPreference.objects.create(panel=self.panel, linked_host=self.host)
        self.assert_stale(row)
        row = self.row()
        pref.revision = 'new-revision'
        pref.save()
        self.assert_stale(row)

    def assert_stale(self, row):
        body = dict(version=row['version'], manual_expiry=None, owner_id=None, followup='pending',
                    reminder_enabled=False, exempt=False, note='stale')
        self.request('post', 'assets/%s/' % row['key'], body, error=True)
        self.assertNotEqual(self.row()['note'], 'stale')

    def test_owner_unavailable_can_be_reassigned_without_access_grant(self):
        owner = self.user('owner')
        row = self.save(owner_id=owner.pk)
        self.assertTrue(row['owner']['available'])
        User.objects.filter(pk=owner.pk).update(is_active=False)
        row = self.row()
        self.assertFalse(row['owner']['available'])
        self.assertEqual(self.save(row, owner_id=None)['owner'], None)
        self.client.defaults['HTTP_X_TOKEN'] = owner.access_token
        self.request('get', 'assets/', error=True)

    def test_every_endpoint_rechecks_admin_active_deleted_and_role(self):
        for changes in ({'is_active': False}, {'is_deleted': True}, {'is_supper': False}):
            User.objects.filter(pk=self.admin.pk).update(is_active=True, is_deleted=False, is_supper=True)
            self.request('get', 'options/')
            User.objects.filter(pk=self.admin.pk).update(**changes)
            for suffix in ('options/', 'assets/', 'reminders/', 'assets/host:%s/history/' % self.host.pk):
                self.request('get', suffix, error=True)
            self.request('post', 'reminders/read/', {'ids': []}, error=True)

    def test_renew_requires_strictly_later_future_date_and_preserves_preferences(self):
        today = date.today()
        current = (today + timedelta(days=60)).isoformat()
        owner = self.user('owner')
        row = self.save(manual_expiry=current, owner_id=owner.pk, reminder_enabled=True, followup='contacted')
        for value in (today.isoformat(), current, (today + timedelta(days=10)).isoformat(), '2030-02-30'):
            self.request('post', 'assets/%s/renew/' % row['key'],
                         dict(version=row['version'], expires_on=value, note='登记'), error=True)
        new_date = (today + timedelta(days=90)).isoformat()
        result = self.request('post', 'assets/%s/renew/' % row['key'],
                              dict(version=row['version'], expires_on=new_date, note='已登记续费'))
        self.assertEqual(result['expires_on'], new_date)
        self.assertEqual(result['followup'], 'pending')
        self.assertTrue(result['reminder_enabled'])
        self.assertEqual(result['owner']['id'], owner.pk)
        history = self.request('get', 'assets/%s/history/' % row['key'])
        self.assertEqual(history['items'][0]['event'], 'renew')
        self.assertNotIn('SECRET', json.dumps(history))

    def test_history_survives_asset_deletion(self):
        row = self.save(note='跟进记录')
        key = row['key']
        self.host.delete()
        history = self.request('get', 'assets/%s/history/' % key)
        self.assertEqual(history['total'], 1)
        self.assertEqual(history['items'][0]['actor_name'], 'admin')

    def test_calendar_boundaries_cumulative_summary_filters_and_nested_groups(self):
        dates = importlib.import_module('apps.ops.expiry.dates')
        fixed = date(2026, 10, 25)
        root = Group.objects.create(name='根组')
        child = Group.objects.create(name='子组', parent_id=root.pk)
        child.hosts.add(self.host)
        with patch.object(dates, 'platform_today', return_value=fixed):
            for days, state in ((-1, 'expired'), (0, 'within_7'), (7, 'within_7'),
                                (8, 'within_30'), (30, 'within_30'), (31, 'valid')):
                self.original((fixed + timedelta(days=days)).isoformat())
                row = self.row()
                self.assertEqual((row['state'], row['days_remaining']), (state, days))
                result = self.rows(group='host:%s' % root.pk)
                self.assertEqual(result['total'], 1)
                self.assertEqual(result['summary']['within_7'], int(0 <= days <= 7))
                self.assertEqual(result['summary']['within_30'], int(0 <= days <= 30))
                self.assertEqual(self.rows(state='within_30')['total'], int(0 <= days <= 30))
        self.assertEqual(self.rows(q='192.0.2.1')['total'], 2)
        self.assertEqual(self.rows(q="' OR 1=1 --")['total'], 0)

    def test_validation_rejects_unknown_types_dates_and_unbounded_pagination(self):
        for params in ({'page': 0}, {'page': '1.0'}, {'page_size': 101}, {'page_size': -1}, {'page': '01'},
                       {'q': 'x' * 201}, {'source': 'any'}, {'group': 'panel:1 OR 1'}, {'state': 'unknown'},
                       {'owner_id': True}, {'owner_id': -1}, {'followup': 'done'}, {'unknown': 1}):
            self.request('get', 'assets/', params, error=True)
        row = self.row()
        valid = dict(version=row['version'], manual_expiry=None, owner_id=None, followup='pending',
                     reminder_enabled=False, exempt=False, note='')
        for changes in ({'manual_expiry': '2026-2-01'}, {'manual_expiry': '2026-02-30'},
                        {'manual_expiry': '2026-01-01 00:00:00'}, {'owner_id': True}, {'owner_id': 9999},
                        {'followup': 'done'}, {'reminder_enabled': 1}, {'exempt': 'false'}, {'note': 'x' * 2001},
                        {'note': None}, {'version': ''}, {'unknown': 'SECRET'}):
            self.request('post', 'assets/%s/' % row['key'], {**valid, **changes}, error=True)

    def test_reminder_current_stage_only_dedup_and_new_cycle(self):
        self.rows()  # route is the public availability contract
        dates = importlib.import_module('apps.ops.expiry.dates')
        fixed = date(2026, 10, 25)
        with patch.object(dates, 'platform_today', return_value=fixed):
            row = self.save(manual_expiry='2026-10-30', reminder_enabled=True)
            checker = self.checker()
            checker.check_expiry()
            checker.check_expiry()
            result = self.request('get', 'reminders/')
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['items'][0]['stage'], '7d')
            self.assertTrue(result['items'][0]['current'])
            self.assertEqual(self.rows()['unread_count'], 1)
            self.assertEqual(self.request('get', 'options/')['check']['status'], 'ok')
        with patch.object(dates, 'platform_today', return_value=date(2026, 10, 30)):
            checker.check_expiry()
            result = self.request('get', 'reminders/', {'current': '1'})
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['items'][0]['stage'], '1d')
            self.assertEqual(self.request('get', 'reminders/')['total'], 2)
        with patch.object(dates, 'platform_today', return_value=date(2026, 10, 31)):
            checker.check_expiry()
            self.assertEqual(self.request('get', 'reminders/', {'current': '1'})['items'][0]['stage'], 'expired')
            row = self.row()
            row = self.request('post', 'assets/%s/renew/' % row['key'],
                               {'version': row['version'], 'expires_on': '2026-11-04', 'note': ''})
            checker.check_expiry()
            self.assertEqual(self.request('get', 'reminders/')['total'], 4)
            self.assertEqual(self.request('get', 'reminders/', {'current': '1'})['total'], 1)

    def test_read_state_is_personal_and_does_not_mutate_expiry_or_global_notifications(self):
        self.rows()
        row = self.save(manual_expiry=date.today().isoformat(), reminder_enabled=True)
        self.checker().check_expiry()
        result = self.request('get', 'reminders/')
        reminder_id = result['items'][0]['id']
        self.request('post', 'reminders/read/', {'ids': [reminder_id]})
        self.assertEqual(self.request('get', 'reminders/', {'unread': '1'})['total'], 0)
        self.assertEqual(self.row()['expires_on'], row['expires_on'])
        second = self.user('second', True)
        self.client.defaults['HTTP_X_TOKEN'] = second.access_token
        self.assertEqual(self.request('get', 'reminders/')['unread_count'], 1)
        notify = apps.get_model('notify', 'Notify')
        self.assertEqual(notify.objects.count(), 0)

    def test_checker_skips_default_disabled_exempt_invalid_and_paused_panels(self):
        self.rows()
        checker = self.checker()
        self.original(date.today().isoformat())
        checker.check_expiry()
        self.assertEqual(self.request('get', 'reminders/')['total'], 0)
        self.save(reminder_enabled=True, exempt=True)
        panel_row = self.save(self.row('panel:%s' % self.panel.pk), manual_expiry=date.today().isoformat(), reminder_enabled=True)
        ResourceMonitorPreference.objects.create(panel=self.panel, linked_host=self.host)
        checker.check_expiry()
        self.assertEqual(self.request('get', 'reminders/')['total'], 0)
        ResourceMonitorPreference.objects.filter(panel=self.panel).update(linked_host=None)
        checker.check_expiry()
        self.assertEqual(self.request('get', 'reminders/')['total'], 1)
        self.save(self.row(panel_row['key']), reminder_enabled=False)
        self.assertEqual(self.request('get', 'reminders/', {'current': '1'})['total'], 0)

    def test_checker_failure_is_safe_and_preserves_previous_success(self):
        self.rows()
        checker = self.checker()
        checker.check_expiry()
        previous = self.request('get', 'options/')['check']['last_success_at']
        with patch.object(checker, 'generate_reminders', side_effect=RuntimeError('SECRET')):
            checker.check_expiry()
        status = self.request('get', 'options/')['check']
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['last_success_at'], previous)
        self.assertNotIn('SECRET', json.dumps(status))

    def test_reminder_read_validation_and_history_pagination(self):
        self.rows()
        for values in ({'ids': '1'}, {'ids': [True]}, {'ids': [0]}, {'ids': list(range(1, 102))}, {'ids': [], 'all': True}):
            self.request('post', 'reminders/read/', values, error=True)
        for params in ({'unread': 'false'}, {'current': 0}, {'page': 0}, {'unknown': 1}):
            self.request('get', 'reminders/', params, error=True)
        row = self.save(note='one')
        self.save(row, note='two')
        history = self.request('get', 'assets/%s/history/' % row['key'], {'page': 2, 'page_size': 1})
        self.assertEqual(history['total'], 2)
        self.assertEqual(len(history['items']), 1)

    def test_linked_panel_identity_change_invalidates_host_editor(self):
        ResourceMonitorPreference.objects.create(panel=self.panel, linked_host=self.host)
        row = self.row()
        Panel.objects.filter(pk=self.panel.pk).update(url='https://changed.example:8888/new-path')
        self.assert_stale(row)

    def test_transaction_rechecks_stale_user_object_and_owner_after_initial_read(self):
        from . import service
        row = self.row()
        body = dict(version=row['version'], manual_expiry=None, owner_id=None, followup='pending',
                    reminder_enabled=False, exempt=False, note='denied')
        for field in ('is_active', 'is_supper', 'is_deleted'):
            User.objects.filter(pk=self.admin.pk).update(is_active=True, is_supper=True, is_deleted=False)
            User.objects.filter(pk=self.admin.pk).update(**{field: field == 'is_deleted'})
            with self.assertRaises(PermissionError):
                service.save(self.admin, row['key'], body)
        User.objects.filter(pk=self.admin.pk).update(is_active=True, is_supper=True, is_deleted=False)
        owner = self.user('owner')
        User.objects.filter(pk=owner.pk).update(is_active=False)
        with self.assertRaises(ValueError):
            service.save(self.admin, row['key'], {**body, 'owner_id': owner.pk})
        self.assertEqual(apps.get_model('ops', 'ExpiryAudit').objects.count(), 0)

    def test_independent_panel_host_equal_addresses_do_not_merge_or_collect(self):
        self.original('2030-01-01')
        row = self.save(self.row('panel:%s' % self.panel.pk), manual_expiry='2031-01-01')
        self.assertEqual(self.rows()['total'], 2)
        self.assertEqual(self.row()['expires_on'], '2030-01-01')
        self.assertEqual(row['expires_on'], '2031-01-01')

    def test_owner_options_exclude_deleted_and_unavailable_owner_invalidates_editor(self):
        owner = self.user('owner')
        row = self.save(owner_id=owner.pk)
        User.objects.filter(pk=owner.pk).update(is_deleted=True)
        self.assertNotIn(owner.pk, [x['id'] for x in self.request('get', 'options/')['owners']])
        self.assertFalse(self.row()['owner']['available'])
        self.assert_stale(row)

    def test_reminder_get_never_generates_or_marks_read_and_current_changes_on_association(self):
        row = self.save(self.row('panel:%s' % self.panel.pk), manual_expiry=date.today().isoformat(),
                        reminder_enabled=True)
        self.assertEqual(self.request('get', 'reminders/')['total'], 0)
        self.assertEqual(self.rows()['unread_count'], 0)
        self.checker().check_expiry()
        self.assertEqual(self.request('get', 'reminders/', {'current': '1'})['total'], 1)
        ResourceMonitorPreference.objects.create(panel=self.panel, linked_host=self.host)
        self.assertEqual(self.request('get', 'reminders/', {'current': '1'})['total'], 0)
        self.assertEqual(self.request('get', 'reminders/')['unread_count'], 1)
        self.assertEqual(apps.get_model('ops', 'ExpiryReminderRead').objects.count(), 0)

    def test_audit_whitelist_actor_snapshot_and_read_body_validation(self):
        row = self.save(note='记录')
        self.admin.nickname = '修改后姓名'
        self.admin.save()
        result = self.request('get', 'assets/%s/history/' % row['key'])
        self.assertEqual(result['items'][0]['actor_name'], 'admin')
        detail = result['items'][0]['detail']
        self.assertEqual(set(detail), {'before', 'after'})
        self.assertEqual(set(detail['after']), {'manual_expiry', 'owner_id', 'followup', 'reminder_enabled', 'exempt', 'note'})
        self.assertNotIn('SECRET', json.dumps(detail))
        self.request('post', 'reminders/read/', {'ids': [999999]}, error=True)
        self.request('get', 'options/', {'unexpected': 1}, error=True)

    def test_post_rejects_malformed_json_and_query_injection(self):
        self.rows()
        for raw in ('[]', 'null', '{broken', '{"version":null}'):
            response = self.client.post('/ops/expiry/assets/host:%s/' % self.host.pk,
                                         data=raw, content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['error'])
        self.request('get', 'assets/?page=1&page=2', error=True)
        response = self.client.post('/ops/expiry/reminders/read/?all=1', data='{"ids":[]}',
                                     content_type='application/json')
        self.assertTrue(response.json()['error'])

    def test_check_staleness_is_visible_without_running_job(self):
        from .models import ExpiryCheck
        from django.utils import timezone
        ExpiryCheck.objects.create(pk=1, status='ok', last_attempt_at=timezone.now() - timedelta(days=2),
                                   last_success_at=timezone.now() - timedelta(days=2), message='旧检查')
        self.assertEqual(self.request('get', 'options/')['check']['status'], 'stale')
        self.assertEqual(ExpiryCheck.objects.get(pk=1).status, 'ok')

    def test_resolved_views_own_their_transactions_with_atomic_requests(self):
        from django.urls import resolve
        for suffix in ('options/', 'assets/', 'assets/host:1/', 'assets/host:1/renew/',
                       'assets/host:1/history/', 'reminders/', 'reminders/read/'):
            view = resolve('/ops/expiry/' + suffix).func
            self.assertIn('default', getattr(view, '_non_atomic_requests', set()), suffix)

    def test_scheduler_registers_local_daily_job_and_wrapper_cleans_connections(self):
        from apps.schedule.scheduler import Scheduler
        from . import reminders
        scheduler = Scheduler()
        scheduler.scheduler = Mock()
        scheduler._init_builtin_jobs()
        expiry = [call for call in scheduler.scheduler.add_job.call_args_list
                  if call.kwargs.get('id') == 'server-expiry-daily']
        self.assertEqual(len(expiry), 1)
        call = expiry[0]
        self.assertIs(call.args[0], reminders.scheduled_check_expiry)
        self.assertEqual((call.kwargs['hour'], call.kwargs['minute']), (9, 0))
        self.assertTrue(call.kwargs['coalesce'])
        self.assertEqual(call.kwargs['max_instances'], 1)
        with patch.object(reminders, 'check_expiry', side_effect=RuntimeError('failed')), \
                patch.object(reminders.connections, 'close_all') as close:
            with self.assertRaises(RuntimeError):
                reminders.scheduled_check_expiry()
        close.assert_called_once()


class ExpiryDateTests(SimpleTestCase):
    def test_strict_calendar_parsing_and_timezone_even_without_use_tz(self):
        from . import dates
        for value in ('2026-2-01', '2026-02-30', '2026-01-01T00:00:00Z', '2026-01-01 25:00:00', 'soon'):
            self.assertIsNone(dates.parse_original(value))
        self.assertEqual(dates.parse_original('2026-02-01 09:30:00'), date(2026, 2, 1))
        self.assertEqual(dates.platform_today(datetime(2026, 10, 24, 17, tzinfo=dt_timezone.utc)), date(2026, 10, 25))

    def test_all_reminder_stage_boundaries(self):
        from . import dates
        for days, expected in ((-1, 'expired'), (0, '1d'), (1, '1d'), (2, '7d'), (7, '7d'),
                               (8, '30d'), (30, '30d'), (31, None), (None, None)):
            self.assertEqual(dates.reminder_stage(days), expected)


class ExpiryConcurrencyTests(TransactionTestCase):
    """SQLite cannot demonstrate the row-lock guarantees required in production."""
    def setUp(self):
        self.admin = User.objects.create(username='concurrent-admin', nickname='管理员', is_supper=True,
                                        access_token='z' * 32, token_expired=int(time.time()) + 3600,
                                        last_ip='', last_login='', password_hash='')
        self.host = Host.objects.create(name='并发主机', hostname='192.0.2.10', username='root',
                                        port=22, created_by=self.admin)

    def race(self, action):
        barrier = threading.Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return action()
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(run) for _ in range(2)]
            return [job.result(timeout=15) for job in jobs]

    def test_save_retries_deadlock_after_rolling_back_metadata_and_audit(self):
        from . import assets, service
        from .models import ExpiryMetadata, ExpiryAudit
        row = assets.inventory()['host:%s' % self.host.pk]
        payload = dict(version=row['version'], manual_expiry='2030-01-01', owner_id=None,
                       followup='pending', reminder_enabled=False, exempt=False, note='保存')
        create, attempts = ExpiryAudit.objects.create, []
        def audit(**values):
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError(1213, 'Deadlock found when trying to get lock')
            return create(**values)
        with patch.object(ExpiryAudit.objects, 'create', side_effect=audit):
            result = service.save(self.admin, row['key'], payload)
        self.assertEqual(result['note'], '保存')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(ExpiryMetadata.objects.count(), 1)
        self.assertEqual(ExpiryAudit.objects.count(), 1)

    def test_check_retries_deadlock_without_partial_or_duplicate_reminders(self):
        from . import reminders
        from .models import ExpiryMetadata, ExpiryReminder
        other = Host.objects.create(name='另一主机', hostname='192.0.2.11', username='root', created_by=self.admin)
        for host in (self.host, other):
            ExpiryMetadata.objects.create(asset_key='host:%s' % host.pk, manual_expiry=date.today(), reminder_enabled=True)
        create, attempts = ExpiryReminder.objects.get_or_create, []
        def reminder(**values):
            attempts.append(1)
            if len(attempts) == 2:
                raise OperationalError(1213, 'Deadlock found when trying to get lock')
            return create(**values)
        with patch.object(ExpiryReminder.objects, 'get_or_create', side_effect=reminder):
            self.assertTrue(reminders.check_expiry())
        self.assertEqual(len(attempts), 4)
        self.assertEqual(ExpiryReminder.objects.count(), 2)

    def test_lock_retry_is_bounded_and_does_not_retry_other_database_failures(self):
        from . import reminders, service
        from .models import ExpiryMetadata, ExpiryReminder, ExpiryCheck
        ExpiryMetadata.objects.create(asset_key='host:%s' % self.host.pk, manual_expiry=date.today(), reminder_enabled=True)
        for code, attempts in ((1205, 3), (2006, 1)):
            with patch.object(service, 'lock_asset', side_effect=OperationalError(code, 'database failure')) as lock:
                self.assertFalse(reminders.check_expiry())
            self.assertEqual(lock.call_count, attempts)
            self.assertEqual(ExpiryReminder.objects.count(), 0)
            self.assertEqual(ExpiryCheck.objects.get(pk=1).status, 'error')

    def panel(self):
        group = PanelGroup.objects.create(name='并发面板组')
        return Panel.objects.create(name='并发面板', url='http://192.0.2.10:8888', username='fixture',
                                    password_ciphertext='fixture', group=group, created_by=self.admin)

    def concurrent(self, first, second, started):
        def run(action):
            close_old_connections()
            try:
                return action()
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_job = pool.submit(run, first)
            self.assertTrue(started.wait(timeout=5))
            second_job = pool.submit(run, second)
            return first_job.result(timeout=15), second_job.result(timeout=15)

    @skipUnlessDBFeature('has_select_for_update')
    def test_mariadb_monitor_commit_and_reminder_check_both_complete(self):
        from apps.baota.resource_monitor import service as monitor
        from . import reminders
        from .models import ExpiryMetadata, ExpiryReminder
        panel = self.panel()
        ResourceMonitorPreference.objects.create(host=self.host, enabled=True)
        ResourceMonitorPreference.objects.create(panel=panel, linked_host=self.host, enabled=True)
        ExpiryMetadata.objects.create(asset_key='host:%s' % self.host.pk, manual_expiry=date.today(), reminder_enabled=True)
        panel_locked, expiry_panel_attempted = threading.Event(), threading.Event()
        def monitor_query(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'FOR UPDATE' in sql.upper() and 'baota_panels' in sql:
                panel_locked.set()
                self.assertTrue(expiry_panel_attempted.wait(timeout=5))
            return result
        def expiry_query(execute, sql, params, many, context):
            if 'FOR UPDATE' in sql.upper() and 'baota_panels' in sql:
                expiry_panel_attempted.set()
            return execute(sql, params, many, context)
        def sample():
            with connections['default'].execute_wrapper(monitor_query):
                return monitor.sample(self.admin, {'key': 'host:%s' % self.host.pk})
        def check():
            with connections['default'].execute_wrapper(expiry_query):
                return reminders.check_expiry()
        with patch.object(monitor, 'collect_panel', return_value={}), patch.object(monitor, 'collect_host', return_value={}):
            sampled, checked = self.concurrent(sample, check, panel_locked)
        self.assertEqual(sampled['snapshot']['status'], 'ok')
        self.assertTrue(checked)
        self.assertEqual(ExpiryReminder.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_mariadb_link_and_reminder_check_recompute_one_canonical_asset(self):
        from apps.baota.resource_monitor import service as monitor
        from . import reminders
        from .models import ExpiryMetadata, ExpiryReminder
        panel = self.panel()
        ResourceMonitorPreference.objects.create(panel=panel)
        for key in ('host:%s' % self.host.pk, 'panel:%s' % panel.pk):
            ExpiryMetadata.objects.create(asset_key=key, manual_expiry=date.today(), reminder_enabled=True)
        preference_locked, expiry_host_locked = threading.Event(), threading.Event()
        invalidate = monitor._invalidate
        def pause_link(preference):
            if not preference_locked.is_set():
                preference_locked.set()
                self.assertTrue(expiry_host_locked.wait(timeout=5))
            return invalidate(preference)
        def expiry_query(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'FOR UPDATE' in sql.upper() and 'hosts' in sql:
                expiry_host_locked.set()
            return result
        def link():
            return monitor.link(self.admin, {'panel_id': panel.pk, 'host_id': self.host.pk})
        def check():
            with connections['default'].execute_wrapper(expiry_query):
                return reminders.check_expiry()
        with patch.object(monitor, '_invalidate', side_effect=pause_link):
            linked, checked = self.concurrent(link, check, preference_locked)
        self.assertEqual(linked, {'ok': True})
        self.assertTrue(checked)
        self.assertEqual(list(ExpiryReminder.objects.values_list('asset_key', flat=True)), ['host:%s' % self.host.pk])

    @skipUnlessDBFeature('has_select_for_update')
    def test_mariadb_two_stale_editors_have_one_winner(self):
        from . import assets, service
        row = assets.inventory()['host:%s' % self.host.pk]
        payload = dict(version=row['version'], manual_expiry='2030-01-01', owner_id=None,
                       followup='pending', reminder_enabled=False, exempt=False, note='保存')
        def write():
            try:
                service.save(self.admin, row['key'], payload)
                return True
            except ValueError:
                return False
        self.assertEqual(sorted(self.race(write)), [False, True])
        self.assertEqual(apps.get_model('ops', 'ExpiryAudit').objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_mariadb_concurrent_cold_reminder_generation_is_unique(self):
        from . import reminders
        from .models import ExpiryMetadata, ExpiryReminder
        ExpiryMetadata.objects.create(asset_key='host:%s' % self.host.pk, manual_expiry=date.today(),
                                       reminder_enabled=True)
        self.race(reminders.generate_reminders)
        self.assertEqual(ExpiryReminder.objects.count(), 1)
