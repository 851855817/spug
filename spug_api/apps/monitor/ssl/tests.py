"""Isolated SSL workflow tests; every remote boundary is a fixture."""
import copy
import importlib
import json
import time
import threading
from datetime import datetime, timedelta, timezone as utc_timezone
from unittest.mock import Mock, patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.account.models import User
from apps.host.models import Host
from apps.baota.models import Panel, PanelGroup, PanelTask


class SSLWorkflowTests(TransactionTestCase):
    def setUp(self):
        self.admin = self.user('admin')
        self.host = Host.objects.create(name='origin', hostname='origin.example.com', port=22,
                                       username='root', pkey='SECRET', is_verified=True, created_by=self.admin)
        self.key = 'host:' + str(self.host.pk)

    def user(self, name, superuser=True):
        return User.objects.create(username=name, nickname=name, is_supper=superuser,
            access_token=(name + 'x' * 32)[:32], token_expired=int(time.time()) + 3600,
            last_ip='', last_login='', password_hash='')

    def modules(self):
        from apps.monitor import models
        self.assertTrue(hasattr(models, 'SSLSource'), 'SSL workflow models missing')
        return (importlib.import_module('apps.monitor.ssl.service'),
                importlib.import_module('apps.monitor.ssl.jobs'),
                importlib.import_module('apps.monitor.ssl.renewal'))

    def enable(self, reminders=False):
        service, _, _ = self.modules()
        return service.configure(self.admin, {'key': self.key, 'revision': '', 'enabled': True,
            'reminders': reminders, 'interval_hours': 6})

    def fixture(self, expiry=None, fingerprint='a' * 64):
        expiry = expiry or datetime.now(utc_timezone.utc) + timedelta(days=5)
        cert = {'fingerprint': fingerprint, 'domains': ['example.com'],
                'not_before': '2026-01-01T00:00:00+00:00', 'expires_at': expiry.isoformat()}
        return {'items': [{'domain': 'example.com', 'port': 443, 'site_id': '/etc/nginx/site.conf:1',
            'site_name': 'example.com', 'cert_path': '/etc/ssl/example/fullchain.pem', 'certificate': cert,
            'deployment_verified': True,
            'renewal': {'engine': 'certbot', 'supported': True, 'reason': '',
                'summary': {'engine': 'certbot', 'domains': ['example.com'], 'reload': 'nginx',
                            'cert_path': '/etc/ssl/example/fullchain.pem', 'impacts': ['example.com']},
                'plan': {'engine': 'certbot', 'order': 'example.com', 'config_digest': 'a' * 64}}}], 'warnings': []}

    def check(self, fixture=None):
        service, jobs, _ = self.modules()
        data = fixture or self.fixture()
        queued = service.enqueue_checks(self.admin, {'source_keys': [self.key]})['jobs'][0]
        with patch.object(jobs, 'discover', return_value=copy.deepcopy(data)), patch.object(
                jobs, 'check_tls', return_value={'verified': True, 'certificate': data['items'][0]['certificate'], 'message': ''}):
            jobs.run_pending()
        return service.get_job(self.admin, queued['id'])

    def expire_cooldown(self):
        from apps.monitor.ssl.models import SSLSource
        SSLSource.objects.update(last_attempt_at=timezone.now() - timedelta(hours=7))

    def expired_visibility_fixture(self):
        from apps.monitor.ssl.models import SSLCertificate
        service, _, _ = self.modules()
        self.enable(reminders=True)
        self.check(self.fixture(datetime.now(utc_timezone.utc) - timedelta(days=20)))
        cert = SSLCertificate.objects.get(source_id=self.key)
        return service, cert, service.certificate_cycle(cert)

    def test_visibility_is_per_account_reversible_and_independent_of_reminders(self):
        from apps.monitor.ssl.models import SSLReminderPreference, SSLCertificate
        service, cert, cycle = self.expired_visibility_fixture()
        before = service.reminders(self.admin, {})
        other = self.user('other')
        service.set_visibility(self.admin, cert.pk, {'hidden': True, 'cycle': cycle})
        visible = service.listing(self.admin, {})
        self.assertEqual((visible['total'], visible['stats']['expired'], visible['hidden_count']), (0, 0, 1))
        hidden = service.listing(self.admin, {'hidden': '1'})
        self.assertEqual(hidden['total'], 1)
        self.assertTrue(hidden['items'][0]['hidden'])
        self.assertEqual(hidden['items'][0]['cycle'], cycle)
        self.assertEqual(service.listing(other, {})['total'], 1)
        self.assertEqual(service.reminders(self.admin, {}), before)
        self.assertFalse(SSLReminderPreference.objects.exists())
        self.assertEqual(SSLCertificate.objects.count(), 1)
        service.set_visibility(self.admin, cert.pk, {'hidden': False, 'cycle': cycle})
        self.assertEqual(service.listing(self.admin, {})['total'], 1)
        self.assertEqual(service.listing(self.admin, {'hidden': '1'})['total'], 0)

    def test_visibility_filters_and_pagination_exclude_only_selected_record(self):
        from apps.monitor.ssl.models import SSLCertificate
        service, cert, cycle = self.expired_visibility_fixture()
        second = SSLCertificate.objects.get(pk=cert.pk)
        second.pk = None
        second.identity = 'b' * 64
        second.domain = 'second.example.com'
        second.local_certificate = {**second.local_certificate, 'domains': ['second.example.com']}
        second.save()
        service.set_visibility(self.admin, cert.pk, {'hidden': True, 'cycle': cycle})
        listing = service.listing(self.admin, {'state': 'expired', 'page_size': '1'})
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['id'], second.pk)
        self.assertEqual(listing['stats']['expired'], 1)
        self.assertEqual(service.listing(self.admin, {'q': 'second'})['hidden_count'], 0)
        self.assertEqual(service.listing(self.admin, {'hidden': '1', 'source': self.key})['total'], 1)
        self.assertEqual(service.listing(self.admin, {'hidden': '1', 'state': 'normal'})['total'], 0)
        with self.assertRaises(ValueError):
            service.listing(self.admin, {'hidden': 'true'})

    def test_visibility_new_certificate_cycle_reappears_without_get_writes(self):
        from apps.setting.models import UserSetting
        service, cert, cycle = self.expired_visibility_fixture()
        service.set_visibility(self.admin, cert.pk, {'hidden': True, 'cycle': cycle})
        pref_before = list(UserSetting.objects.values())
        cert.local_certificate = {**cert.local_certificate, 'fingerprint': 'c' * 64,
                                  'expires_at': (datetime.now(utc_timezone.utc) + timedelta(days=90)).isoformat()}
        cert.save()
        fresh = service.listing(self.admin, {})
        self.assertEqual(fresh['total'], 1)
        self.assertFalse(fresh['items'][0]['hidden'])
        self.assertEqual(fresh['hidden_count'], 0)
        self.assertNotEqual(fresh['items'][0]['cycle'], cycle)
        self.assertEqual(service.listing(self.admin, {'hidden': '1'})['total'], 0)
        self.assertEqual(list(UserSetting.objects.values()), pref_before)
        with self.assertRaises(ValueError):
            service.set_visibility(self.admin, cert.pk, {'hidden': True, 'cycle': cycle})

    def test_visibility_rejects_nonexpired_invalid_and_revoked_requests(self):
        service, cert, cycle = self.expired_visibility_fixture()
        for payload in ({'hidden': 'true', 'cycle': cycle}, {'hidden': True},
                        {'hidden': True, 'cycle': 'invalid'}, {'hidden': True, 'cycle': cycle, 'user_id': 2}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service.set_visibility(self.admin, cert.pk, payload)
        cert.local_certificate = {**cert.local_certificate,
                                  'expires_at': (datetime.now(utc_timezone.utc) + timedelta(days=90)).isoformat()}
        cert.save()
        with self.assertRaises(ValueError):
            service.set_visibility(self.admin, cert.pk, {'hidden': True, 'cycle': service.certificate_cycle(cert)})
        User.objects.filter(pk=self.admin.pk).update(is_supper=False)
        with self.assertRaises(PermissionError):
            service.set_visibility(self.admin, cert.pk, {'hidden': False, 'cycle': service.certificate_cycle(cert)})

    def test_visibility_hidden_records_can_be_restored_when_source_is_disabled(self):
        from apps.monitor.ssl.models import SSLSource
        service, cert, cycle = self.expired_visibility_fixture()
        service.set_visibility(self.admin, cert.pk, {'hidden': True, 'cycle': cycle})
        SSLSource.objects.filter(pk=self.key).update(enabled=False)
        hidden = service.listing(self.admin, {'hidden': '1'})['items'][0]
        self.assertTrue(hidden['stale'])
        service.set_visibility(self.admin, cert.pk, {'hidden': False, 'cycle': cycle})
        self.assertEqual(service.listing(self.admin, {})['total'], 1)

    def test_visibility_get_does_not_create_preferences_or_contact_sources(self):
        from apps.setting.models import UserSetting
        service, jobs, _ = self.modules()
        with patch.object(jobs, 'discover') as remote:
            service.listing(self.admin, {})
            service.listing(self.admin, {'hidden': '1'})
        remote.assert_not_called()
        self.assertFalse(UserSetting.objects.exists())

    def test_visibility_http_boundary_is_local_and_accepts_only_post(self):
        from apps.setting.models import UserSetting
        service, cert, cycle = self.expired_visibility_fixture()
        self.client.defaults['HTTP_X_TOKEN'] = self.admin.access_token
        path = '/monitor/ssl/certificates/' + str(cert.pk) + '/visibility/'
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertFalse(UserSetting.objects.exists())
        reply = self.client.post(path, json.dumps({'hidden': True, 'cycle': cycle}), content_type='application/json').json()
        self.assertFalse(reply.get('error'), reply)
        self.assertEqual(service.listing(self.admin, {})['total'], 0)

    def test_default_disabled_get_is_local_and_does_not_create_preferences(self):
        service, _, _ = self.modules()
        self.assertFalse(service.sources(self.admin)['items'][0]['enabled'])
        self.assertEqual(service.sources(self.admin)['items'][0]['revision'], '')
        self.assertEqual(service.listing(self.admin, {})['items'], [])
        from apps.monitor.ssl.models import SSLSource, SSLLease
        self.assertEqual(SSLSource.objects.count(), 0)
        self.assertEqual(SSLLease.objects.count(), 0)

    def test_active_superadmin_is_reread_and_source_cas(self):
        service, _, _ = self.modules()
        self.enable()
        with self.assertRaises(ValueError):
            service.configure(self.admin, {'key': self.key, 'revision': '', 'enabled': False,
                                           'reminders': False, 'interval_hours': 6})
        User.objects.filter(pk=self.admin.pk).update(is_supper=False)
        with self.assertRaises(PermissionError):
            service.sources(self.admin)

    def test_first_source_without_snapshot_is_pending_not_configuration_changed(self):
        saved = self.enable()
        self.assertEqual(saved['status'], 'never_checked')
        self.assertEqual(saved['message'], '尚未检查')

    def test_first_failed_scan_shows_actual_reason_in_source_and_public_job(self):
        from apps.monitor.ssl.collectors import CollectorError
        service, jobs, _ = self.modules()
        self.enable()
        queued = service.enqueue_checks(self.admin, {'source_keys': [self.key]})['jobs'][0]
        with patch.object(jobs, 'discover', side_effect=CollectorError('include_missing')):
            jobs.run_pending()
        source = service.sources(self.admin)['items'][0]
        self.assertEqual(source['status'], 'stale')
        self.assertIn('引用', source['message'])
        self.assertIn('不存在', source['message'])
        result = service.get_job(self.admin, queued['id'])
        self.assertEqual(result['result']['sources'][0]['message'], source['message'])
        self.assertIsNone(source['last_success_at'])

    def test_enqueue_is_durable_single_admission_and_never_calls_remote(self):
        service, jobs, _ = self.modules()
        self.enable()
        with patch.object(jobs, 'discover') as remote:
            service.enqueue_checks(self.admin, {'source_keys': [self.key]})
            with self.assertRaises(ValueError):
                service.enqueue_checks(self.admin, {'source_keys': [self.key]})
            remote.assert_not_called()

    def test_failed_collection_preserves_facts_marks_stale_and_sanitizes_errors(self):
        service, jobs, _ = self.modules()
        self.enable()
        self.assertEqual(self.check()['state'], 'success')
        old = service.listing(self.admin, {})['items'][0]
        self.expire_cooldown()
        service.enqueue_checks(self.admin, {'source_keys': [self.key]})
        with patch.object(jobs, 'discover', side_effect=RuntimeError('SECRET remote output')):
            jobs.run_pending()
        result = service.listing(self.admin, {})
        self.assertTrue(result['items'][0]['stale'])
        self.assertEqual(result['items'][0]['sampled_at'], old['sampled_at'])
        self.assertEqual(result['stats']['normal'], 0)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_exact_expiry_and_cdn_are_independent(self):
        service, jobs, _ = self.modules()
        self.enable()
        self.check(self.fixture(datetime.now(utc_timezone.utc) - timedelta(seconds=1)))
        row = service.listing(self.admin, {})['items'][0]
        self.assertEqual(row['state'], 'expired')
        self.assertLess(row['days_remaining'], 0)
        self.assertEqual(row['expires_at'], row['local_certificate']['expires_at'])

    def test_source_change_or_disable_during_read_discards_results(self):
        service, jobs, _ = self.modules()
        configured = self.enable()
        service.enqueue_checks(self.admin, {'source_keys': [self.key]})
        def remote(*args):
            service.configure(self.admin, {'key': self.key, 'revision': configured['revision'],
                'enabled': False, 'reminders': False, 'interval_hours': 6})
            return self.fixture()
        with patch.object(jobs, 'discover', side_effect=remote):
            jobs.run_pending()
        self.assertEqual(service.listing(self.admin, {})['items'], [])

    def test_revoked_owner_before_worker_never_contacts_server(self):
        service, jobs, _ = self.modules()
        self.enable()
        queued = service.enqueue_checks(self.admin, {'source_keys': [self.key]})['jobs'][0]
        User.objects.filter(pk=self.admin.pk).update(is_active=False)
        with patch.object(jobs, 'discover') as remote:
            jobs.run_pending()
            remote.assert_not_called()
        from apps.monitor.ssl.models import SSLJob
        self.assertEqual(SSLJob.objects.get(pk=queued['id']).state, 'cancelled')

    def test_reminders_are_per_account_per_certificate_cycle_and_require_fresh_check(self):
        service, _, _ = self.modules()
        self.enable(reminders=True)
        self.check()
        own = service.reminders(self.admin, {})
        self.assertEqual(len(own['items']), 1)
        other = self.user('other')
        service.mute_reminder(self.admin, own['items'][0]['id'], {'muted': True})
        self.assertEqual(service.reminders(self.admin, {})['unread_count'], 0)
        self.assertEqual(service.reminders(self.admin, {})['items'], [])
        self.assertEqual(service.reminders(other, {})['unread_count'], 1)
        self.expire_cooldown()
        self.check()
        self.assertEqual(service.reminders(other, {})['total'], 1)
        self.expire_cooldown()
        self.check(self.fixture(fingerprint='b' * 64))
        self.assertEqual(service.reminders(other, {})['total'], 1)
        self.assertEqual(service.reminders(self.admin, {})['unread_count'], 1)

    def prepare_renewal(self):
        service, jobs, renewal = self.modules()
        self.enable()
        self.check()
        cert = service.listing(self.admin, {})['items'][0]
        prepared = renewal.prepare(self.admin, {'certificate_id': cert['id']})
        with patch.object(jobs, 'discover', return_value=self.fixture()):
            jobs.run_pending()
        return service.get_job(self.admin, prepared['id'])

    def test_renew_preflight_requires_exact_acks_and_owner_and_server_admission(self):
        service, _, renewal = self.modules()
        prepared = self.prepare_renewal()
        self.assertEqual(prepared['state'], 'prepared')
        with self.assertRaises(ValueError):
            renewal.prepare(self.admin, {'certificate_id': prepared['certificate_id']})
        with self.assertRaises(PermissionError):
            renewal.start(self.user('other'), prepared['id'], {'revision': prepared['revision'], 'acknowledgements': []})
        with self.assertRaises(ValueError):
            renewal.start(self.admin, prepared['id'], {'revision': prepared['revision'], 'acknowledgements': []})
        self.assertNotIn('plan', json.dumps(prepared))

    def start_renewal(self, prepared):
        _, _, renewal = self.modules()
        return renewal.start(self.admin, prepared['id'], {'revision': prepared['revision'],
            'acknowledgements': [a['code'] for a in prepared['acknowledgements']]})

    def test_runtime_config_drift_blocks_send(self):
        service, jobs, _ = self.modules()
        prepared = self.prepare_renewal()
        self.start_renewal(prepared)
        Host.objects.filter(pk=self.host.pk).update(pkey='CHANGED')
        with patch.object(jobs, 'discover') as discover, patch.object(jobs, 'renew_host') as send:
            jobs.run_pending()
            discover.assert_not_called()
            send.assert_not_called()
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'blocked')

    def test_disable_after_persisted_intent_prevents_send(self):
        service, jobs, _ = self.modules()
        prepared = self.prepare_renewal()
        self.start_renewal(prepared)
        def intent_hook(job):
            from apps.monitor.ssl.models import SSLSource
            SSLSource.objects.filter(key=self.key).update(enabled=False)
        with patch.object(jobs, 'discover', return_value=self.fixture()), patch.object(
                jobs, 'after_intent', side_effect=intent_hook), patch.object(jobs, 'renew_host') as send:
            jobs.run_pending()
            send.assert_not_called()
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'uncertain')

    def test_restart_after_intent_is_uncertain_and_verify_never_resends(self):
        service, jobs, renewal = self.modules()
        prepared = self.prepare_renewal()
        from apps.monitor.ssl.models import SSLJob
        SSLJob.objects.filter(pk=prepared['id']).update(state='running', phase='dispatch',
            intent={'at': timezone.now().isoformat()}, lease_until=timezone.now() - timedelta(seconds=1))
        with patch.object(jobs, 'renew_host') as send:
            jobs.run_pending()
            self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'uncertain')
            renewal.verify(self.admin, prepared['id'], {})
            with patch.object(jobs, 'discover', return_value=self.fixture()), patch.object(jobs, 'check_tls',
                    return_value={'verified': True, 'certificate': self.fixture()['items'][0]['certificate'], 'message': ''}):
                jobs.run_pending()
            send.assert_not_called()

    def test_restart_after_intent_readonly_new_certificates_resolve_and_release_admission(self):
        service, jobs, renewal = self.modules()
        prepared = self.prepare_renewal()
        self.start_renewal(prepared)
        from apps.monitor.ssl.models import SSLJob, SSLLease
        SSLJob.objects.filter(pk=prepared['id']).update(state='running', phase='dispatch',
            intent={'at': timezone.now().isoformat()}, lease_until=timezone.now() - timedelta(seconds=1))
        after = self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=90), 'b' * 64)
        with patch.object(jobs, 'renew_host') as send:
            jobs.run_pending()
            self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'uncertain')
            renewal.verify(self.admin, prepared['id'], {})
            with patch.object(jobs, 'discover', return_value=after), patch.object(jobs, 'check_tls',
                    return_value={'verified': True, 'certificate': after['items'][0]['certificate']}):
                jobs.run_pending()
            send.assert_not_called()
        resolved = service.get_job(self.admin, prepared['id'])
        self.assertEqual(resolved['state'], 'verified')
        self.assertEqual(resolved['message'], '已核实本地与公网证书更新，服务重载过程未获确认；未重新续签')
        self.assertTrue(resolved['result']['local_updated'])
        self.assertTrue(resolved['result']['public_updated'])
        self.assertFalse(resolved['result']['reload_verified'])
        lease = SSLLease.objects.get(pk='renew:' + self.key)
        self.assertIsNone(lease.job_id)
        self.assertEqual(lease.token, '')
        self.assertNotIn(prepared['id'], [job['id'] for job in service.listing(self.admin, {})['active_jobs']])
        self.assertEqual(renewal.prepare(self.admin, {'certificate_id': prepared['certificate_id']})['state'], 'queued')

    def test_readonly_verify_without_both_new_certificates_remains_uncertain(self):
        service, jobs, renewal = self.modules()
        prepared = self.prepare_renewal()
        from apps.monitor.ssl.models import SSLJob, SSLLease
        original = SSLJob.objects.get(pk=prepared['id']).baseline['certificate']
        updated = self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=90), 'b' * 64)
        same_expiry = copy.deepcopy(updated)
        same_expiry['items'][0]['certificate']['expires_at'] = original['expires_at']
        unchanged = self.fixture()
        unchanged['items'][0]['certificate'] = original
        cases = ((updated, original, True), (unchanged, updated['items'][0]['certificate'], False),
                 (unchanged, original, False), (same_expiry, same_expiry['items'][0]['certificate'], False))
        for local, public, local_updated in cases:
            with self.subTest(local=local['items'][0]['certificate'], public=public):
                SSLJob.objects.filter(pk=prepared['id']).update(state='uncertain', intent={'at': 'sent'}, result={})
                renewal.verify(self.admin, prepared['id'], {})
                with patch.object(jobs, 'discover', return_value=local), patch.object(jobs, 'check_tls',
                        return_value={'verified': True, 'certificate': public}), patch.object(jobs, 'renew_host') as send:
                    jobs.run_pending()
                    send.assert_not_called()
                result = service.get_job(self.admin, prepared['id'])
                self.assertEqual(result['state'], 'uncertain')
                self.assertEqual(result['result']['local_updated'], local_updated)
                self.assertFalse(result['result']['public_updated'])
                self.assertFalse(result['result']['reload_verified'])
                self.assertEqual(SSLLease.objects.get(pk='renew:' + self.key).job_id, prepared['id'])
                with self.assertRaises(ValueError):
                    renewal.prepare(self.admin, {'certificate_id': prepared['certificate_id']})

    def test_api_boundary_sanitizes_unknown_exception(self):
        self.modules()
        self.client.defaults['HTTP_X_TOKEN'] = self.admin.access_token
        with patch('apps.monitor.ssl.service.sources', side_effect=RuntimeError('SECRET')):
            payload = self.client.get('/monitor/ssl/sources/').json()
        self.assertTrue(payload['error'])
        self.assertNotIn('SECRET', json.dumps(payload))

    def test_empty_scheduler_tick_keeps_all_additive_tables_empty(self):
        _, jobs, _ = self.modules()
        from apps.monitor.ssl import models
        with patch.object(jobs, 'discover') as remote:
            self.assertFalse(jobs.run_pending())
            remote.assert_not_called()
        for cls in (models.SSLSource, models.SSLCertificate, models.SSLJob, models.SSLLease,
                    models.SSLReminder, models.SSLReminderPreference):
            self.assertEqual(cls.objects.count(), 0, cls.__name__)

    def test_expired_admission_lease_does_not_admit_second_queued_check(self):
        service, _, _ = self.modules()
        self.enable()
        service.enqueue_checks(self.admin, {'source_keys': [self.key]})
        from apps.monitor.ssl.models import SSLLease
        SSLLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.expire_cooldown()
        with self.assertRaises(ValueError):
            service.enqueue_checks(self.admin, {'source_keys': [self.key]})

    def test_two_certificates_on_same_server_cannot_have_two_renewals(self):
        service, jobs, renewal = self.modules()
        self.enable()
        data = self.fixture()
        other = copy.deepcopy(data['items'][0])
        other['domain'], other['site_id'] = 'www.example.com', 'other-site'
        data['items'].append(other)
        self.check(data)
        certs = service.listing(self.admin, {})['items']
        renewal.prepare(self.admin, {'certificate_id': certs[0]['id']})
        with self.assertRaises(ValueError):
            renewal.prepare(self.admin, {'certificate_id': certs[1]['id']})

    def test_changed_plan_after_confirm_prevents_selected_order_send(self):
        service, jobs, _ = self.modules()
        prepared = self.prepare_renewal()
        self.start_renewal(prepared)
        changed = self.fixture()
        changed['items'][0]['renewal']['plan']['order'] = 'unselected-order'
        with patch.object(jobs, 'discover', return_value=changed), patch.object(jobs, 'renew_host') as send:
            jobs.run_pending()
            send.assert_not_called()
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'blocked')

    def test_oversize_source_fails_without_silent_truncation(self):
        service, jobs, _ = self.modules()
        self.enable()
        data = self.fixture()
        data['items'] *= 101
        self.assertEqual(self.check(data)['state'], 'failed')
        self.assertEqual(service.listing(self.admin, {})['items'], [])

    def test_source_failure_never_emits_new_expiry_reminder_from_stale_facts(self):
        service, jobs, _ = self.modules()
        self.enable(reminders=True)
        self.check()
        from apps.monitor.ssl.models import SSLReminder
        before = SSLReminder.objects.count()
        self.expire_cooldown()
        service.enqueue_checks(self.admin, {'source_keys': [self.key]})
        with patch.object(jobs, 'discover', side_effect=RuntimeError('SECRET')):
            jobs.run_pending()
        self.assertEqual(SSLReminder.objects.count(), before)
        self.assertEqual(service.reminders(self.admin, {})['items'], [])

    def test_panel_runtime_authorization_preserves_unlinked_tasks_and_blocks_revoked_source(self):
        service, _, renewal = self.modules()
        prepared = self.prepare_renewal()
        task = PanelTask.objects.create(operation='ssl_renew', owner=self.admin)
        self.assertTrue(renewal.panel_runtime_authorized(task.pk))
        from apps.monitor.ssl.models import SSLJob, SSLSource
        SSLJob.objects.filter(pk=prepared['id']).update(panel_task=task, state='queued', phase='panel_wait', intent={'at': 'now'})
        self.assertTrue(renewal.panel_runtime_authorized(task.pk))
        SSLSource.objects.filter(pk=self.key).update(enabled=False)
        self.assertFalse(renewal.panel_runtime_authorized(task.pk))
        SSLSource.objects.filter(pk=self.key).update(enabled=True)
        User.objects.filter(pk=self.admin.pk).update(is_supper=False)
        self.assertFalse(renewal.panel_runtime_authorized(task.pk))

    def test_linked_panel_read_preflight_also_stops_after_source_disable(self):
        _, _, renewal = self.modules()
        prepared = self.prepare_renewal()
        task = PanelTask.objects.create(operation='ssl_renew', owner=self.admin)
        from apps.monitor.ssl.models import SSLJob, SSLSource
        SSLJob.objects.filter(pk=prepared['id']).update(panel_task=task, state='queued', phase='panel_preflight')
        self.assertTrue(renewal.panel_runtime_authorized(task.pk, mutating=False))
        self.assertFalse(renewal.panel_runtime_authorized(task.pk, mutating=True))
        SSLSource.objects.filter(pk=self.key).update(enabled=False)
        self.assertFalse(renewal.panel_runtime_authorized(task.pk, mutating=False))
        SSLSource.objects.filter(pk=self.key).update(enabled=True)
        User.objects.filter(pk=self.admin.pk).update(is_supper=False)
        self.assertFalse(renewal.panel_runtime_authorized(task.pk, mutating=False))

    def test_orphaned_marked_panel_task_fails_closed(self):
        _, _, renewal = self.modules()
        task = PanelTask.objects.create(operation='ssl_renew', owner=self.admin,
                                       audit=[{'action': 'ssl_monitor', 'job_id': 999}])
        self.assertFalse(renewal.panel_runtime_authorized(task.pk))

    def test_lost_worker_cannot_start_additional_source_remote_call(self):
        service, jobs, _ = self.modules()
        self.enable()
        queued = service.enqueue_checks(self.admin, {'source_keys': [self.key]})['jobs'][0]
        from apps.monitor.ssl.models import SSLJob
        job = SSLJob.objects.get(pk=queued['id'])
        with patch.object(jobs, 'discover') as remote:
            with self.assertRaises(ValueError):
                jobs.collect_source(job, self.key)
            remote.assert_not_called()

    def test_baota_uses_selected_site_and_original_panel_sync_ack(self):
        service, jobs, renewal = self.modules()
        group = PanelGroup.objects.create(name='group')
        panel = Panel.objects.create(name='panel', url='https://panel.example.com', username='admin',
            password_ciphertext='SECRET', api_base_url='https://panel.example.com', api_key_ciphertext='SECRET',
            api_checked_at='2026-11-04 10:00:00', api_version='13.1.0', group=group, created_by=self.admin)
        self.key = 'panel:' + str(panel.pk)
        self.enable()
        data = self.fixture()
        data['items'][0]['site_id'] = 17
        data['items'][0]['renewal'].update(engine='baota', plan={})
        self.check(data)
        cert = service.listing(self.admin, {})['items'][0]
        prepared = renewal.prepare(self.admin, {'certificate_id': cert['id']})
        with patch.object(jobs, 'discover', return_value=data):
            jobs.run_pending()
        job = service.get_job(self.admin, prepared['id'])
        task = PanelTask.objects.get(pk=job['panel_task_id'])
        self.assertEqual(task.params_by_panel[str(panel.pk)]['sites'], [17])
        self.assertNotIn('all_sites', task.params_by_panel[str(panel.pk)])
        target = task.targets.get()
        target.children = [{'state': 'ready', 'snapshot': {'state': 'ready', 'plan': {
            'required_acknowledgements': ['ssl_panel_sync']}}, 'intents': []}]
        target.state = 'prepared'
        target.save()
        task.state = 'prepared'
        task.save()
        jobs.run_pending()
        job = service.get_job(self.admin, prepared['id'])
        self.assertEqual([a['code'] for a in job['acknowledgements']], ['ssl_panel_sync'])
        self.assertEqual(job['state'], 'prepared')
        task.state = 'completed'
        task.save()
        target.state = 'completed'
        target.children[0]['state'] = 'success'
        target.save()
        from apps.monitor.ssl.models import SSLJob
        SSLJob.objects.filter(pk=job['id']).update(state='queued', phase='panel_wait', intent={'at': 'sent'})
        with patch.object(jobs, 'verify_renewal') as verify:
            jobs.run_pending()
            verify.assert_called_once()
            self.assertTrue(verify.call_args.kwargs['reload_verified'])

    def test_worker_mutation_client_rechecks_revocation_before_actual_request(self):
        from apps.baota import worker
        self.assertTrue(hasattr(worker, 'SSLAuthorizedClient'), 'SSL send guard missing')
        client, task = Mock(), Mock(pk=99)
        guarded = worker.SSLAuthorizedClient(client, task)
        with patch('apps.monitor.ssl.renewal.panel_runtime_authorized', return_value=False):
            with self.assertRaises(Exception):
                guarded.request('/acme', 'renew', {}, mutating=True)
        client.request.assert_not_called()

    def test_expired_running_lease_cannot_overwrite_recovered_job(self):
        service, jobs, _ = self.modules()
        prepared = self.prepare_renewal()
        from apps.monitor.ssl.models import SSLJob
        SSLJob.objects.filter(pk=prepared['id']).update(state='running', phase='dispatch', lease_token='old',
            lease_until=timezone.now() - timedelta(seconds=1), intent={'at': 'sent'})
        stale = SSLJob.objects.get(pk=prepared['id'])
        jobs.recover_expired()
        jobs.finish(stale, 'success', 'old worker returns too late')
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'uncertain')

    def test_preflight_disable_between_read_and_persist_never_prepares(self):
        service, jobs, _ = self.modules()
        self.enable()
        self.check()
        cert = service.listing(self.admin, {})['items'][0]
        from apps.monitor.ssl import renewal
        queued = renewal.prepare(self.admin, {'certificate_id': cert['id']})
        def remote(*args):
            from apps.monitor.ssl.models import SSLSource
            SSLSource.objects.filter(key=self.key).update(enabled=False)
            return self.fixture()
        with patch.object(jobs, 'discover', side_effect=remote):
            jobs.run_pending()
        self.assertEqual(service.get_job(self.admin, queued['id'])['state'], 'blocked')

    def test_unknown_protocol_ssl_facts_cannot_claim_normal_for_wildcard(self):
        service, _, _ = self.modules()
        self.enable()
        data = self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=50))
        data['items'][0]['domain'] = '*.example.com'
        data['items'][0]['certificate']['domains'] = ['*.example.com']
        self.check(data)
        row = service.listing(self.admin, {})['items'][0]
        self.assertEqual(row['state'], 'unknown')

    def test_expiring_wildcard_keeps_local_expiry_and_reminder_without_renewal(self):
        service, _, _ = self.modules()
        self.enable(reminders=True)
        data = self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=5))
        data['items'][0]['domain'] = '*.example.com'
        data['items'][0]['certificate']['domains'] = ['*.example.com']
        self.check(data)
        row = service.listing(self.admin, {})['items'][0]
        self.assertEqual(row['state'], 'expiring')
        self.assertGreater(row['days_remaining'], 0)
        self.assertLess(row['days_remaining'], 10)
        self.assertEqual(row['expires_at'], data['items'][0]['certificate']['expires_at'])
        self.assertEqual(service.reminders(self.admin, {})['unread_count'], 1)
        self.assertFalse(row['renewal']['supported'])
        self.assertIn('具体', row['renewal']['reason'])
        self.assertIn('SAN', row['renewal']['reason'])

    def test_direct_wildcard_renew_post_rejects_before_server_admission(self):
        service, jobs, _ = self.modules()
        self.enable()
        data = self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=5))
        data['items'][0]['domain'] = '*.example.com'
        data['items'][0]['certificate']['domains'] = ['*.example.com']
        self.check(data)
        row = service.listing(self.admin, {})['items'][0]
        from apps.monitor.ssl.models import SSLCertificate, SSLJob, SSLLease
        cert = SSLCertificate.objects.get(pk=row['id'])
        self.assertTrue(cert.renewal['supported'])
        initial_job = cert.latest_job_id
        self.client.defaults['HTTP_X_TOKEN'] = self.admin.access_token
        with patch.object(jobs, 'discover') as remote:
            payload = self.client.post('/monitor/ssl/renew/', json.dumps({'certificate_id': cert.pk}),
                                       content_type='application/json').json()
            remote.assert_not_called()
        self.assertTrue(payload['error'])
        self.assertIn('具体', payload['error'])
        self.assertIn('SAN', payload['error'])
        self.assertFalse(SSLJob.objects.filter(kind='renew').exists())
        self.assertFalse(SSLLease.objects.filter(key__startswith='renew:').exists())
        self.assertEqual(PanelTask.objects.count(), 0)
        cert.refresh_from_db()
        self.assertEqual(cert.latest_job_id, initial_job)

    def test_certificate_detail_exposes_validated_deployment_path(self):
        service, _, _ = self.modules()
        self.enable()
        data = self.fixture()
        self.check(data)
        row = service.listing(self.admin, {})['items'][0]
        self.assertEqual(row.get('cert_path'), data['items'][0]['cert_path'])

    def test_panel_includes_keeps_specific_readonly_renewal_reason(self):
        service, _, _ = self.modules()
        capability = service.safe_renewal({'engine': 'baota', 'supported': False,
                                          'reason_code': 'unverified_includes'})
        self.assertFalse(capability['supported'])
        self.assertEqual(capability['reason'],
            '已核对源站证书，配置引用尚未完整核实，请从既有面板任务预检续签或关联 SSH 检查')

    def test_verify_is_publicly_queued_and_exact_tls_diagnostics_are_safe(self):
        service, _, renewal = self.modules()
        prepared = self.prepare_renewal()
        from apps.monitor.ssl.models import SSLJob
        SSLJob.objects.filter(pk=prepared['id']).update(state='uncertain', intent={'at': 'sent'})
        queued = renewal.verify(self.admin, prepared['id'], {})
        self.assertEqual(queued['state'], 'queued')
        self.assertEqual(queued['phase'], 'verify')
        for message in ('公网 DNS 解析失败', '公网检查拒绝私有、回环及特殊网络地址',
                        '公网 TLS 连接或握手失败', '公网证书验证失败：证书链、域名或有效期不可信'):
            self.assertEqual(service.safe_tls({'verified': False, 'certificate': {}, 'message': message})['message'], message)
        self.assertNotIn('SECRET', service.safe_tls({'message': 'SECRET remote trace'})['message'])

    def test_public_job_result_does_not_forward_internal_or_unknown_fields(self):
        service, _, _ = self.modules()
        from apps.monitor.ssl.models import SSLJob
        job = SSLJob.objects.create(owner=self.admin, kind='renew', result={
            'plan': {'password': 'SECRET'}, 'local_updated': True, 'public_updated': False,
            'transport_error': 'SECRET', 'local_certificate': self.fixture()['items'][0]['certificate']})
        result = service.get_job(self.admin, job.pk)['result']
        self.assertTrue(result['local_updated'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_selected_order_unchanged_is_safe_finished_result_and_does_not_block_forever(self):
        service, jobs, _ = self.modules()
        prepared = self.prepare_renewal()
        self.start_renewal(prepared)
        with patch.object(jobs, 'discover', return_value=self.fixture()), patch.object(jobs, 'renew_host',
                return_value={'state': 'unchanged', 'changed': False, 'certificates': []}):
            jobs.run_pending()
        job = service.get_job(self.admin, prepared['id'])
        self.assertEqual(job['state'], 'failed')
        self.assertIn('续签窗口', job['message'])

    def test_default_schedule_is_six_hours_and_does_not_repeat_during_cooldown(self):
        service, jobs, _ = self.modules()
        self.enable()
        data = self.fixture()
        with patch.object(jobs, 'discover', return_value=data), patch.object(jobs, 'check_tls', return_value={
                'verified': True, 'certificate': data['items'][0]['certificate']}):
            jobs.run_pending()
        from apps.monitor.ssl.models import SSLJob, SSLSource
        self.assertEqual(SSLJob.objects.count(), 1)
        self.assertEqual(SSLSource.objects.get(pk=self.key).interval_hours, 6)
        jobs.schedule_due()
        self.assertEqual(SSLJob.objects.count(), 1)

    def test_mariadb_two_accounts_have_single_check_admission(self):
        if connection.vendor != 'mysql':
            self.skipTest('Requires MariaDB row locks; SQLite cannot prove cross-process admission')
        service, _, _ = self.modules()
        self.enable()
        other, barrier, results, errors = self.user('other'), threading.Barrier(2), [], []
        def enqueue(actor):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                service.enqueue_checks(actor, {'source_keys': [self.key]})
                results.append('accepted')
            except ValueError:
                results.append('blocked')
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                connection.close()
        threads = [threading.Thread(target=enqueue, args=(actor,)) for actor in (self.admin, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), ['accepted', 'blocked'])

    def test_unverified_deployment_keeps_dates_unknown_without_reminder_or_renewal(self):
        service, _, renewal = self.modules()
        self.enable(reminders=True)
        data = self.fixture()
        data['items'][0]['deployment_verified'] = False
        self.check(data)
        row = service.listing(self.admin, {})['items'][0]
        self.assertEqual(row['state'], 'unknown')
        self.assertTrue(row['expires_at'])
        self.assertFalse(row['renewal']['supported'])
        self.assertEqual(service.reminders(self.admin, {})['items'], [])
        with self.assertRaises(ValueError):
            renewal.prepare(self.admin, {'certificate_id': row['id']})

    def test_successful_send_then_old_public_certificate_stays_uncertain_until_readback(self):
        service, jobs, renewal = self.modules()
        prepared = self.prepare_renewal()
        self.start_renewal(prepared)
        before, after = self.fixture(), self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=90), 'b' * 64)
        with patch.object(jobs, 'discover', side_effect=[before, after]), patch.object(jobs, 'renew_host',
                return_value={'state': 'success', 'changed': True}) as send, patch.object(jobs, 'check_tls',
                return_value={'verified': True, 'certificate': before['items'][0]['certificate']}):
            jobs.run_pending()
            send.assert_called_once()
        result = service.get_job(self.admin, prepared['id'])
        self.assertEqual(result['state'], 'uncertain')
        self.assertTrue(result['result']['local_updated'])
        self.assertFalse(result['result']['public_updated'])
        self.assertTrue(result['result']['reload_verified'])
        renewal.verify(self.admin, result['id'], {})
        with patch.object(jobs, 'discover', return_value=after), patch.object(jobs, 'renew_host') as send, patch.object(
                jobs, 'check_tls', return_value={'verified': True, 'certificate': after['items'][0]['certificate']}):
            jobs.run_pending()
            send.assert_not_called()
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'success')

    def test_waiting_panel_job_does_not_starve_later_check(self):
        service, jobs, _ = self.modules()
        self.enable()
        from apps.monitor.ssl.models import SSLJob
        waiting = SSLJob.objects.create(owner=self.admin, kind='renew', phase='panel_preflight')
        queued = service.enqueue_checks(self.admin, {'source_keys': [self.key]})['jobs'][0]
        def wait(job):
            jobs.finish(job, 'queued', '等待面板预检', phase='panel_preflight')
        with patch.object(jobs, 'sync_panel_preflight', side_effect=wait), patch.object(jobs, 'run_check') as check:
            jobs.run_pending()
            jobs.run_pending()
            check.assert_called_once()
            self.assertEqual(check.call_args.args[0].pk, queued['id'])

    def test_total_check_budget_stops_before_later_sources_and_reports_incomplete(self):
        service, jobs, _ = self.modules()
        from apps.monitor.ssl.models import SSLJob
        job = SSLJob.objects.create(owner=self.admin, kind='check', state='running', lease_token='token',
            lease_until=timezone.now() + timedelta(minutes=10), source_keys=['host:' + str(v) for v in range(1, 6)])
        elapsed = [0]
        def collect(job, key, **kwargs):
            elapsed[0] += 130
            return {'source_key': key, 'state': 'success', 'count': 0}
        with patch.object(jobs.time, 'monotonic', side_effect=lambda: elapsed[0]), patch.object(
                jobs, 'collect_source', side_effect=collect) as remote:
            jobs.run_check(job)
        self.assertEqual(remote.call_count, 3)
        saved = service.get_job(self.admin, job.pk)
        self.assertEqual(saved['state'], 'failed')
        self.assertEqual(len(saved['result']['sources']), 5)
        self.assertEqual(saved['result']['sources'][3]['state'], 'failed')

    def test_mute_and_restore_apply_whole_account_certificate_cycle_across_stages(self):
        service, _, _ = self.modules()
        self.enable(reminders=True)
        self.check()
        first = service.reminders(self.admin, {})['items'][0]
        service.mute_reminder(self.admin, first['id'], {'muted': True})
        from apps.monitor.ssl.models import SSLReminder
        original = SSLReminder.objects.get(pk=first['id'])
        later = SSLReminder.objects.create(certificate=original.certificate, cycle=original.cycle,
                                           stage='3', expires_at=original.expires_at)
        self.assertEqual(service.reminders(self.admin, {})['unread_count'], 0)
        row = service.reminders(self.admin, {'muted': '1'})['items'][0]
        self.assertEqual(row['id'], later.pk)
        self.assertEqual(service.listing(self.admin, {})['items'][0]['reminder'], {'id': later.pk, 'muted': True})
        other = self.user('other')
        self.assertEqual(service.reminders(other, {})['unread_count'], 1)
        service.mute_reminder(self.admin, row['id'], {'muted': False})
        self.assertEqual(service.reminders(self.admin, {})['unread_count'], 1)

    def test_certificate_list_exposes_only_current_account_reminder_and_get_never_writes(self):
        from apps.monitor.ssl.models import SSLReminderPreference
        service, _, _ = self.modules()
        self.enable(reminders=True)
        self.check()
        reminder = service.reminders(self.admin, {})['items'][0]
        before = SSLReminderPreference.objects.count()
        self.assertEqual(service.listing(self.admin, {})['items'][0]['reminder'],
                         {'id': reminder['id'], 'muted': False})
        self.assertEqual(SSLReminderPreference.objects.count(), before)
        service.mute_reminder(self.admin, reminder['id'], {'muted': True})
        self.assertEqual(service.listing(self.admin, {})['items'][0]['reminder'],
                         {'id': reminder['id'], 'muted': True})
        self.assertFalse(service.listing(self.user('other'), {})['items'][0]['reminder']['muted'])
        service.mute_reminder(self.admin, reminder['id'], {'muted': False})
        self.assertFalse(service.listing(self.admin, {})['items'][0]['reminder']['muted'])

    def test_certificate_list_does_not_offer_mute_without_a_current_valid_reminder(self):
        service, _, _ = self.modules()
        self.enable(reminders=False)
        self.check()
        self.assertIsNone(service.listing(self.admin, {})['items'][0]['reminder'])
        self.expire_cooldown()
        from apps.monitor.ssl.models import SSLSource
        SSLSource.objects.update(reminders=True)
        self.check()
        self.assertIsNotNone(service.listing(self.admin, {})['items'][0]['reminder'])
        SSLSource.objects.update(status='failed')
        self.assertIsNone(service.listing(self.admin, {})['items'][0]['reminder'])

    def test_certificate_list_reminder_resets_after_certificate_cycle_changes(self):
        service, _, _ = self.modules()
        self.enable(reminders=True)
        self.check()
        first = service.reminders(self.admin, {})['items'][0]
        service.mute_reminder(self.admin, first['id'], {'muted': True})
        self.expire_cooldown()
        self.check(self.fixture(fingerprint='b' * 64))
        current = service.listing(self.admin, {})['items'][0]['reminder']
        self.assertNotEqual(current['id'], first['id'])
        self.assertFalse(current['muted'])

    def test_owner_can_cancel_unused_preflight_and_admit_new_renewal(self):
        service, _, renewal = self.modules()
        self.assertTrue(callable(getattr(renewal, 'cancel', None)), 'prepared cancellation missing')
        prepared = self.prepare_renewal()
        with self.assertRaises(PermissionError):
            renewal.cancel(self.user('other'), prepared['id'], {})
        cancelled = renewal.cancel(self.admin, prepared['id'], {})
        self.assertEqual(cancelled['state'], 'cancelled')
        created = renewal.prepare(self.admin, {'certificate_id': prepared['certificate_id']})
        self.assertEqual(created['state'], 'queued')

    def test_unused_preflight_expires_after_thirty_minutes_but_intent_does_not_cancel(self):
        service, jobs, renewal = self.modules()
        prepared = self.prepare_renewal()
        from apps.monitor.ssl.models import SSLJob
        SSLJob.objects.filter(pk=prepared['id']).update(updated_at=timezone.now() - timedelta(minutes=31))
        jobs.recover_expired()
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'cancelled')
        self.assertIn('30', service.get_job(self.admin, prepared['id'])['message'])
        SSLJob.objects.filter(pk=prepared['id']).update(state='prepared', intent={'at': 'sent'},
            updated_at=timezone.now() - timedelta(minutes=31))
        jobs.recover_expired()
        self.assertEqual(service.get_job(self.admin, prepared['id'])['state'], 'prepared')
        with self.assertRaises(ValueError):
            renewal.cancel(self.admin, prepared['id'], {})

    def test_prepared_cancel_requires_no_source_authorization_after_config_drift(self):
        service, _, renewal = self.modules()
        self.assertTrue(callable(getattr(renewal, 'cancel', None)), 'prepared cancellation missing')
        prepared = self.prepare_renewal()
        Host.objects.filter(pk=self.host.pk).update(pkey='CHANGED')
        self.assertEqual(renewal.cancel(self.admin, prepared['id'], {})['state'], 'cancelled')

    def test_same_source_configuration_is_noop_and_other_admin_does_not_take_ownership(self):
        service, _, renewal = self.modules()
        prepared = self.prepare_renewal()
        configured = service.sources(self.admin)['items'][0]
        other = self.user('other')
        saved = service.configure(other, {key: configured[key] for key in
            ('key', 'revision', 'enabled', 'reminders', 'interval_hours')})
        self.assertEqual(saved['revision'], configured['revision'])
        from apps.monitor.ssl.models import SSLSource
        self.assertEqual(SSLSource.objects.get(pk=self.key).owner_id, self.admin.pk)
        self.start_renewal(prepared)

    def test_noop_does_not_mask_changed_source_or_revoked_configuration_owner(self):
        service, _, _ = self.modules()
        self.enable()
        self.check()
        configured = service.sources(self.admin)['items'][0]
        Host.objects.filter(pk=self.host.pk).update(pkey='CHANGED')
        saved = service.configure(self.admin, {key: configured[key] for key in
            ('key', 'revision', 'enabled', 'reminders', 'interval_hours')})
        self.assertNotEqual(saved['revision'], configured['revision'])
        self.assertEqual(saved['status'], 'invalidated')

    def test_reauthorizing_revoked_source_owner_does_not_mark_old_cache_fresh(self):
        service, _, _ = self.modules()
        self.enable()
        self.check()
        configured = service.sources(self.admin)['items'][0]
        other = self.user('other')
        User.objects.filter(pk=self.admin.pk).update(is_supper=False)
        saved = service.configure(other, {key: configured[key] for key in
            ('key', 'revision', 'enabled', 'reminders', 'interval_hours')})
        self.assertNotEqual(saved['revision'], configured['revision'])
        self.assertNotEqual(saved['status'], 'ok')
        self.assertEqual(service.listing(other, {})['stats']['normal'], 0)

    def test_local_domain_mismatch_cannot_be_normal_even_when_public_tls_is_valid(self):
        service, jobs, _ = self.modules()
        self.enable()
        data = self.fixture(datetime.now(utc_timezone.utc) + timedelta(days=90))
        data['items'][0]['certificate']['domains'] = ['other.example.com']
        self.check(data)
        row = service.listing(self.admin, {})['items'][0]
        self.assertEqual(row['state'], 'error')
        self.assertIn('域名', row['message'])

    def test_missing_certificate_is_unconfigured_and_san_wildcards_are_single_label(self):
        service, _, _ = self.modules()
        self.enable()
        data = self.fixture()
        data['items'][0].update(certificate={}, deployment_verified=False)
        self.check(data)
        self.assertEqual(service.listing(self.admin, {})['items'][0]['state'], 'unconfigured')
        self.assertTrue(callable(getattr(service, 'certificate_covers', None)), 'local SAN matcher missing')
        self.assertTrue(service.certificate_covers('www.example.com', ['*.example.com']))
        self.assertFalse(service.certificate_covers('a.b.example.com', ['*.example.com']))
        self.assertFalse(service.certificate_covers('example.com', ['*.example.com']))
        self.assertFalse(service.certificate_covers('*.example.com', ['example.com']))

    def test_missing_remote_python_has_specific_fixed_diagnostic(self):
        service, _, _ = self.modules()
        error = RuntimeError('SECRET')
        error.code = 'python_missing'
        self.assertIn('Python 3', service.safe_error(error))
        self.assertNotIn('SECRET', service.safe_error(error))

    def test_panel_download_guards_recheck_read_authorization(self):
        from apps.baota.worker import SSLAuthorizedClient
        client, task = Mock(), Mock(pk=99)
        wrapped = SSLAuthorizedClient(client, task)
        for method in ('read_site_web_config', 'read_site_public_certificate', 'read_issued_certificate'):
            with self.subTest(method=method), patch('apps.monitor.ssl.renewal.panel_runtime_authorized', return_value=False) as auth:
                with self.assertRaises(Exception):
                    getattr(wrapped, method)('safe')
                auth.assert_called_once_with(99, mutating=False)
                getattr(client, method).assert_not_called()
