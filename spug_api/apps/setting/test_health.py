import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import RequestFactory, SimpleTestCase, override_settings


class PlatformHealthTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings_override = override_settings(
            PLATFORM_HEALTH_DIR=str(self.root/'state'), BAOTA_KEY_FILE=str(self.root/'baota.key'),
            PLATFORM_HEALTH_DATA_DIR=str(self.root), PLATFORM_HOST_HEALTH_FILE=str(self.root/'host.json'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def health(self):
        from apps.setting import health
        return health

    def test_admin_required_for_both_read_and_start(self):
        from apps.setting.health_views import PlatformHealthView
        for method in ('get', 'post'):
            request = (RequestFactory().get('/setting/health/') if method == 'get' else
                       RequestFactory().post('/setting/health/', data='{}', content_type='application/json'))
            request.user = SimpleNamespace(is_supper=False, pk=2)
            with patch('apps.setting.health.snapshot') as snapshot:
                data = json.loads(PlatformHealthView.as_view()(request).content)
            self.assertTrue(data['error'])
            snapshot.assert_not_called()

    def test_snapshot_is_bounded_and_never_returns_exception_or_secret(self):
        health = self.health()
        with patch.object(health, 'run_probe', side_effect=RuntimeError('password=secret-value')):
            result = health.snapshot()
        self.assertEqual(result['status'], 'error')
        self.assertGreaterEqual(len(result['checks']), 6)
        self.assertNotIn('secret-value', json.dumps(result))
        self.assertTrue(result['checked_at'].endswith('+00:00'))

    def test_probe_timeout_has_no_raw_exception_output(self):
        health = self.health()
        with patch.object(health.subprocess, 'run', side_effect=subprocess.TimeoutExpired('secret-token', 4)):
            result = health.run_probe('database')
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('secret-token', json.dumps(result))

    def test_probe_rejects_unknown_names_without_spawning(self):
        health = self.health()
        with patch.object(health.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                health.run_probe('database; rm -rf /')
        run.assert_not_called()

    def test_key_missing_changed_and_valid_have_distinct_safe_results(self):
        health = self.health()
        key = self.root/'baota.key'
        marker = Path(str(key)+'.sha256')
        self.assertEqual(health.check_key()['status'], 'error')
        value = Fernet.generate_key()
        key.write_bytes(value)
        key.chmod(0o600)
        self.assertEqual(health.check_key()['status'], 'error')
        marker.write_text('0'*64)
        self.assertEqual(health.check_key()['status'], 'error')
        marker.write_text(hashlib.sha256(value).hexdigest())
        good = health.check_key()
        self.assertEqual(good['status'], 'ok')
        self.assertNotIn(value.decode(), json.dumps(good))
        self.assertNotIn(hashlib.sha256(value).hexdigest(), json.dumps(good))
        key.write_bytes(value + b'\n')
        self.assertEqual(health.check_key()['status'], 'ok')

    def test_disk_low_space_and_inode_usage_are_visible(self):
        health = self.health()
        usage = SimpleNamespace(total=10*1024**3, used=9*1024**3, free=300*1024**2)
        fs = SimpleNamespace(f_files=1000, f_favail=5)
        with patch.object(health.shutil, 'disk_usage', return_value=usage), patch.object(health.os, 'statvfs', return_value=fs, create=True):
            checks = health.check_disk()
        self.assertTrue(all(item['status'] == 'error' for item in checks))

    def test_supervisor_recovery_refuses_running_starting_unknown_and_redis(self):
        health = self.health()
        for state in ('RUNNING', 'STARTING', 'BACKOFF', 'UNKNOWN', 'STOPPING', None):
            with patch.object(health, 'service_states', return_value={'spug-worker': state}), patch.object(health.subprocess, 'run') as run:
                result = health.recover_service('spug-worker', 1)
            self.assertEqual(result['status'], 'refused', state)
            run.assert_not_called()
        with patch.object(health.subprocess, 'run') as run:
            result = health.recover_service('redis', 1)
        self.assertEqual(result['status'], 'refused')
        run.assert_not_called()

    def test_start_only_named_stopped_service_and_audit_actor(self):
        health = self.health()
        with patch.object(health, 'service_states', side_effect=[{'spug-worker': 'STOPPED'}, {'spug-worker': 'RUNNING'}]), patch.object(health.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            result = health.recover_service('spug-worker', 17)
        self.assertEqual(result['status'], 'started')
        self.assertEqual(run.call_args.args[0][-2:], ['start', 'spug-worker'])
        self.assertFalse(run.call_args.kwargs.get('shell', False))
        audit = [json.loads(line) for line in (self.root/'state'/'recovery.jsonl').read_text().splitlines()]
        self.assertEqual(audit[-1]['actor_id'], 17)
        self.assertEqual(audit[-1]['service'], 'spug-worker')

    def test_start_timeout_is_uncertain_and_not_retried(self):
        health = self.health()
        with patch.object(health, 'service_states', return_value={'spug-worker': 'EXITED'}), patch.object(health.subprocess, 'run', side_effect=subprocess.TimeoutExpired('secret', 4)) as run:
            result = health.recover_service('spug-worker', 1)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('secret', json.dumps(result))

    def test_concurrent_recovery_is_refused_without_start(self):
        health = self.health()
        with health.operation_lock():
            with patch.object(health.subprocess, 'run') as run:
                result = health.recover_service('spug-worker', 1)
        self.assertEqual(result['status'], 'refused')
        run.assert_not_called()

    def test_recovery_cannot_race_with_an_inflight_snapshot(self):
        health = self.health()
        with health.operation_lock('snapshot'):
            with patch.object(health, 'service_states', side_effect=[{'spug-worker': 'STOPPED'}, {'spug-worker': 'RUNNING'}]), patch.object(
                    health.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                result = health.recover_service('spug-worker', 1)
        self.assertEqual(result['status'], 'refused')
        run.assert_not_called()

    def test_malformed_action_never_reaches_recovery(self):
        from apps.setting.health_views import PlatformHealthView
        for body in ('[]', '{', '{"action":"restart","service":"spug-api"}', '{"action":"start","service":["spug-api"]}'):
            request = RequestFactory().post('/setting/health/', data=body, content_type='application/json')
            request.user = SimpleNamespace(is_supper=True, pk=1)
            with patch('apps.setting.health.recover_service') as recover:
                response = PlatformHealthView.as_view()(request)
            self.assertTrue(json.loads(response.content)['error'])
            recover.assert_not_called()

    def test_supervisor_parser_missing_service_is_unknown_never_recoverable(self):
        from apps.setting.health_probe import parse_services
        result = parse_services('spug-worker RUNNING pid 1, uptime 1:00\nredis STOPPED Not started\nextra RUNNING password=secret')
        by_name = {item['service']: item for item in result['checks']}
        self.assertEqual(len(by_name), 9)
        self.assertEqual(by_name['spug-api']['status'], 'unknown')
        self.assertFalse(by_name['spug-api']['recoverable'])
        self.assertFalse(by_name['redis']['recoverable'])
        self.assertEqual(by_name['spug-ops-worker']['process_state'], 'UNKNOWN')
        self.assertNotIn('secret', json.dumps(result))

    def test_stale_or_untrusted_host_report_does_not_become_green(self):
        health = self.health()
        report = self.root/'host.json'
        report.write_text(json.dumps({'status':'ok', 'checked_at':'2020-01-01T00:00:00+00:00', 'checks':[]}))
        self.assertEqual(health.check_host_report()['status'], 'warning')
        report.write_text('password=secret-value')
        result = health.check_host_report()
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('secret-value', json.dumps(result))

    def test_snapshot_cache_expires_and_does_not_repeat_probes_within_15_seconds(self):
        health = self.health()
        calls = []
        def probe(kind):
            calls.append(kind)
            return {'id': kind, 'name': kind, 'status': 'ok', 'summary': 'ok', 'advice': ''}
        with patch.object(health, 'run_probe', side_effect=probe):
            first = health.snapshot()
            second = health.snapshot()
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 3)
            with patch.object(health, 'now', return_value=health.datetime(2030, 1, 1, tzinfo=health.timezone.utc)):
                third = health.snapshot()
            self.assertNotEqual(first['checked_at'], third['checked_at'])
            self.assertEqual(len(calls), 6)

    def test_no_start_if_preoperation_audit_fails(self):
        health = self.health()
        with patch.object(health, 'service_states', return_value={'spug-worker': 'STOPPED'}), patch.object(
                health, 'audit', side_effect=OSError('secret-path')), patch.object(health.subprocess, 'run') as run:
            result = health.recover_service('spug-worker', 1)
        self.assertEqual(result['status'], 'refused')
        run.assert_not_called()
        self.assertNotIn('secret-path', json.dumps(result))

    def test_recent_empty_host_report_does_not_become_green(self):
        health = self.health()
        (self.root/'host.json').write_text(json.dumps({'checked_at': health.now().isoformat(), 'status': 'ok', 'checks': []}))
        self.assertEqual(health.check_host_report()['status'], 'unknown')

    def test_cli_utc_timestamp_is_compatible_with_python39(self):
        health = self.health()
        checked_at = health.now().isoformat().replace('+00:00', 'Z')
        (self.root/'host.json').write_text(json.dumps({'checked_at': checked_at, 'status': 'ok', 'checks': [
            {'id': 'host.http', 'status': 'ok'}]}))
        real_datetime = health.datetime
        class Python39Datetime:
            @staticmethod
            def fromisoformat(value):
                if value.endswith('Z'):
                    raise ValueError('Python 3.9 does not support Z')
                return real_datetime.fromisoformat(value)
        with patch.object(health, 'datetime', Python39Datetime), patch.object(health, 'now', return_value=real_datetime.now(health.timezone.utc)):
            self.assertEqual(health.check_host_report()['status'], 'ok')


class HostReportValidationTests(SimpleTestCase):
    # Explicit expected values also cover warning taking precedence over unknown.
    AGGREGATES = (
        (('ok',), 'ok'), (('unknown',), 'unknown'),
        (('warning',), 'warning'), (('error',), 'error'),
        (('ok', 'unknown'), 'unknown'), (('ok', 'warning'), 'warning'),
        (('ok', 'error'), 'error'), (('unknown', 'warning'), 'warning'),
        (('unknown', 'error'), 'error'), (('warning', 'error'), 'error'),
        (('ok', 'unknown', 'warning'), 'warning'),
        (('ok', 'unknown', 'error'), 'error'),
        (('ok', 'warning', 'error'), 'error'),
        (('unknown', 'warning', 'error'), 'error'),
        (('ok', 'unknown', 'warning', 'error'), 'error'),
    )

    def setUp(self):
        from apps.setting import health
        self.health = health
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'host.json'
        settings_override = override_settings(PLATFORM_HOST_HEALTH_FILE=str(self.path))
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.current_time = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
        clock = patch.object(health, 'now', return_value=self.current_time)
        clock.start()
        self.addCleanup(clock.stop)

    def report(self, status='ok', statuses=('ok',)):
        return {'checked_at': self.current_time.isoformat(), 'status': status,
                'checks': [{'id': 'host.check.%d' % index, 'status': value}
                           for index, value in enumerate(statuses)]}

    def check_report(self, report):
        self.path.write_text(json.dumps(report), encoding='utf-8')
        return self.health.check_host_report()

    def assert_invalid(self, report):
        self.assertEqual(self.check_report(report), {
            'id': 'host', 'name': '宿主机独立诊断', 'status': 'unknown',
            'summary': '未取得有效宿主机报告',
            'advice': '通过 SSH 执行独立诊断并检查报告同步。',
        })

    def test_healthy_top_level_cannot_hide_a_failed_check(self):
        report = self.report(statuses=('error',))
        report['checks'][0]['id'] = 'host.http'
        self.assert_invalid(report)

    def test_matching_aggregate_statuses_are_accepted_regardless_of_order(self):
        for statuses, expected in self.AGGREGATES:
            for ordered in (statuses, tuple(reversed(statuses))):
                with self.subTest(statuses=ordered):
                    result = self.check_report(self.report(expected, ordered))
                    self.assertEqual(result['status'], expected)
                    self.assertIn('独立报告：', result['summary'])

    def test_each_mismatched_aggregate_status_is_invalid(self):
        for statuses, expected in self.AGGREGATES:
            for top_status in ('ok', 'warning', 'error', 'unknown'):
                if top_status != expected:
                    with self.subTest(statuses=statuses, top_status=top_status):
                        self.assert_invalid(self.report(top_status, statuses))

    def test_checks_must_be_a_nonempty_list_of_at_most_32_objects(self):
        for checks in (None, False, 1, 'private-token', {}, [],
                       [None], [False], [1], ['private-token'], [[]],
                       [{'id': 'host.%d' % index, 'status': 'ok'} for index in range(33)]):
            with self.subTest(checks=checks):
                report = self.report()
                report['checks'] = checks
                self.assert_invalid(report)
        report = self.report()
        del report['checks']
        self.assert_invalid(report)

    def test_check_ids_are_required_unique_and_bounded_ascii(self):
        invalid_ids = (None, False, 1, [], {}, '', ' ', 'host/http',
                       'host:private-token', 'host.检查', 'hóst',
                       'host.http\n', 'host.\tcheck', 'a' * 101)
        for check_id in invalid_ids:
            with self.subTest(check_id=check_id):
                report = self.report()
                report['checks'][0]['id'] = check_id
                self.assert_invalid(report)
        report = self.report()
        del report['checks'][0]['id']
        self.assert_invalid(report)

    def test_duplicate_check_ids_are_invalid(self):
        report = self.report(statuses=('ok', 'ok'))
        report['checks'][1]['id'] = report['checks'][0]['id']
        self.assert_invalid(report)

    def test_check_statuses_are_required_and_allowlisted(self):
        for status in (None, False, 1, [], {}, '', 'OK', 'private-token'):
            with self.subTest(status=status):
                report = self.report()
                report['checks'][0]['status'] = status
                self.assert_invalid(report)
        report = self.report()
        del report['checks'][0]['status']
        self.assert_invalid(report)

    def test_top_level_statuses_are_required_and_allowlisted(self):
        for status in (None, False, 1, [], {}, '', 'OK', 'private-token'):
            with self.subTest(status=status):
                self.assert_invalid(self.report(status))
        report = self.report()
        del report['status']
        self.assert_invalid(report)

    def test_minimal_report_and_valid_id_and_count_boundaries_are_compatible(self):
        for ids in (('host.http',), ('a',), ('Az09_.-' + 'a' * 93,),
                    tuple('host.%d' % index for index in range(32))):
            with self.subTest(ids=ids):
                report = self.report()
                report['checks'] = [{'id': check_id, 'status': 'ok'} for check_id in ids]
                self.assertEqual(self.check_report(report)['status'], 'ok')

    def test_report_content_is_never_echoed_for_valid_or_invalid_reports(self):
        report = self.report()
        report['private-token'] = 'sensitive body'
        report['checks'][0].update(id='private-token', name='private-token',
                                   summary='private-token', advice='private-token')
        result = self.check_report(report)
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('private-token', json.dumps(result))
        report['checks'][0]['status'] = 'private-token'
        self.assert_invalid(report)

    def test_freshness_boundaries_and_utc_z_remain_compatible(self):
        for age, expected in ((-60.001, 'warning'), (-60, 'ok'),
                              (0, 'ok'), (600, 'ok'), (600.001, 'warning')):
            for suffix in ('offset', 'Z'):
                with self.subTest(age=age, suffix=suffix):
                    report = self.report()
                    checked_at = (self.current_time - timedelta(seconds=age)).isoformat()
                    report['checked_at'] = checked_at.replace('+00:00', 'Z') if suffix == 'Z' else checked_at
                    result = self.check_report(report)
                    self.assertEqual(result['status'], expected)
                    if expected == 'warning':
                        self.assertEqual(result['summary'], '宿主机报告已过期或时间异常')

    def test_byte_limit_accepts_131072_bytes_and_rejects_larger_reports(self):
        report = self.report()
        report['ignored'] = ''
        raw = json.dumps(report)
        report['ignored'] = 'x' * (131072 - len(raw.encode('utf-8')))
        self.assertEqual(self.check_report(report)['status'], 'ok')
        self.assertEqual(self.path.stat().st_size, 131072)
        report['ignored'] += 'x'
        self.assert_invalid(report)

    def test_malformed_reports_and_read_errors_keep_the_safe_unknown_result(self):
        for report in (None, [], False, 1, 'private-token'):
            with self.subTest(report=report):
                self.assert_invalid(report)
        for checked_at in (None, False, 1, [], {}, 'private-token', '2026-10-25T00:00:00'):
            with self.subTest(checked_at=checked_at):
                report = self.report()
                report['checked_at'] = checked_at
                self.assert_invalid(report)
        for raw in ('password=private-token', '{', '\ufeff{}'):
            self.path.write_text(raw, encoding='utf-8')
            self.assertEqual(self.health.check_host_report()['summary'], '未取得有效宿主机报告')
        with patch.object(Path, 'read_text', side_effect=OSError('private-token')):
            result = self.health.check_host_report()
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('private-token', json.dumps(result))

    def test_full_host_report_shape_remains_compatible(self):
        ids = ('host.container.spug', 'host.container.spug-db',
               'host.service.nginx', 'host.service.redis', 'host.service.spug-api',
               'host.service.spug-monitor', 'host.service.spug-panel-worker',
               'host.service.spug-scheduler', 'host.service.spug-worker',
               'host.service.spug-ws', 'host.service.spug-ops-worker',
               'host.http', 'host.disk', 'host.memory', 'host.backup')
        report = self.report()
        report['checked_at'] = self.current_time.isoformat().replace('+00:00', 'Z')
        report['checks'] = [{'id': check_id, 'name': 'offline fixture', 'status': 'ok',
                             'summary': 'private-token', 'advice': 'private-token'}
                            for check_id in ids]
        report['limitations'] = ['private-token']
        result = self.check_report(report)
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('private-token', json.dumps(result))
