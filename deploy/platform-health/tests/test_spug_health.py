import importlib.util
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'spug-health.py'
SPEC = importlib.util.spec_from_file_location('spug_host_health', SCRIPT)
MODULE = None
if SCRIPT.is_file():
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.containers = {'spug': 'running|healthy|false|0', 'spug-db': 'running|healthy|false|0'}
        self.services = {name: 'RUNNING' for name in (
            'nginx', 'redis', 'spug-api', 'spug-monitor', 'spug-panel-worker',
            'spug-scheduler', 'spug-worker', 'spug-ws')}
        self.fail = None
        self.after_start = 'RUNNING'

    def __call__(self, args, timeout=4):
        self.calls.append(tuple(args))
        if self.fail:
            raise self.fail
        if args[:2] == ['docker', 'inspect']:
            return MODULE.CommandResult(0, self.containers[args[-1]])
        if args[:2] == ['docker', 'start']:
            self.containers[args[-1]] = 'running|none|false|0'
            return MODULE.CommandResult(0, args[-1])
        if args[:2] == ['docker', 'cp']:
            return MODULE.CommandResult(0, '')
        if args[:4] == ['docker', 'exec', 'spug', 'supervisorctl']:
            action, service = args[4:]
            if action == 'status':
                return MODULE.CommandResult(0, service + ' ' + self.services[service] + ' private diagnostic details')
            if action == 'start':
                self.services[service] = self.after_start
                return MODULE.CommandResult(0, service + ': started')
        raise AssertionError('Unexpected command')


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The independent host diagnostic CLI is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runner = FakeRunner()
        self.mem = self.root / 'meminfo'
        self.mem.write_text('MemTotal: 100000 kB\nMemAvailable: 75000 kB\n')
        self.diagnostic = MODULE.HostDiagnostic(
            root=self.root, runner=self.runner, http_get=lambda: 200,
            disk_probe=lambda _: (1000, 250, 750, 1000, 750), meminfo_path=self.mem)

    def backup(self, suffix='20261024-120000-paired'):
        folder = self.root / 'backups' / suffix
        folder.mkdir(parents=True)
        values = {'database.sql.gz': b'fake database archive', 'spug-data.tar.gz': b'fake data archive'}
        for name, value in values.items():
            (folder / name).write_bytes(value)
        manifest = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n'
                           for name, value in values.items())
        (folder / 'SHA256SUMS').write_text(manifest)
        return folder

    def check(self, report, name):
        return next(item for item in report['checks'] if item['id'] == name)

    def test_readonly_report_uses_only_allowlisted_docker_fields_and_services(self):
        self.backup()
        report = self.diagnostic.report()
        self.assertEqual(report['status'], 'ok')
        self.assertRegex(report['checked_at'], r'^\d{4}-\d\d-\d\dT.*Z$')
        self.assertEqual(len(report['checks']), 14)
        for item in report['checks']:
            self.assertEqual(set(item), {'id', 'name', 'status', 'summary', 'advice'})
        for call in self.runner.calls:
            self.assertNotIn('start', call)
            self.assertNotIn('restart', call)
            self.assertNotIn('logs', call)
            if call[1] == 'inspect':
                self.assertEqual(call[2], '--format')
                self.assertNotIn('.Config', call[3])
        self.assertEqual(set(self.root.iterdir()), {self.root / 'backups', self.mem})
        self.assertIn('not verified', self.check(report, 'host.backup')['summary'])

    def test_errors_timeouts_and_malicious_docker_output_are_not_echoed(self):
        self.runner.fail = subprocess.TimeoutExpired('secret-password customer-domain', 4,
                                                     output='DB_PASSWORD=private-token')
        report = self.diagnostic.report()
        self.assertEqual(self.check(report, 'host.container.spug')['status'], 'unknown')
        self.assertNotIn('secret-password', json.dumps(report))
        self.assertNotIn('private-token', json.dumps(report))
        self.runner.fail = None
        self.runner.containers['spug'] = 'secret-password|private-token|false|0'
        self.runner.services['nginx'] = 'secret-password'
        report = self.diagnostic.report()
        self.assertNotIn('secret-password', json.dumps(report))
        self.assertNotIn('private-token', json.dumps(report))

    def test_http_failure_does_not_read_response_body_or_error_details(self):
        def failure():
            raise RuntimeError('customer contents and password')
        self.diagnostic.http_get = failure
        report = self.diagnostic.report()
        self.assertEqual(self.check(report, 'host.http')['status'], 'unknown')
        self.assertNotIn('password', json.dumps(report))

    def test_disk_and_memory_thresholds(self):
        self.diagnostic.disk_probe = lambda _: (1000, 960, 40, 1000, 30)
        self.mem.write_text('MemTotal: 100000 kB\nMemAvailable: 3000 kB\n')
        report = self.diagnostic.report()
        self.assertEqual(self.check(report, 'host.disk')['status'], 'error')
        self.assertEqual(self.check(report, 'host.memory')['status'], 'error')

    def test_backup_verifies_both_streamed_digests_but_claims_no_recovery_drill(self):
        folder = self.backup()
        report = self.diagnostic.report(verify_backup=True)
        check = self.check(report, 'host.backup')
        self.assertEqual(check['status'], 'ok')
        self.assertIn('SHA-256', check['summary'])
        self.assertIn('Recovery has not been rehearsed', check['advice'])
        (folder / 'database.sql.gz').write_bytes(b'changed')
        self.assertEqual(self.check(self.diagnostic.report(verify_backup=True), 'host.backup')['status'], 'error')

    def test_latest_incomplete_attempt_is_not_hidden_by_older_pair(self):
        self.backup('20261023-120000-paired')
        latest = self.root / 'backups' / '20261024-120000-paired'
        latest.mkdir()
        (latest / 'database.sql.gz').write_bytes(b'partial')
        self.assertEqual(self.check(self.diagnostic.report(), 'host.backup')['status'], 'warning')

    def test_manifest_paths_duplicates_extra_files_and_large_manifest_are_rejected(self):
        folder = self.backup()
        digest = '0' * 64
        for manifest in (
                digest + '  ../outside\n', digest + '  /etc/shadow\n',
                digest + '  C:\\private\\key\n', digest + '  ./database.sql.gz\n',
                digest + '  database.sql.gz\n' + digest + '  database.sql.gz\n',
                digest + '  database.sql.gz\n' + digest + '  spug-data.tar.gz\n' + digest + '  run.sh\n',
                'x' * 5000):
            with self.subTest(manifest=manifest[:80]):
                (folder / 'SHA256SUMS').write_text(manifest)
                report = self.diagnostic.report(verify_backup=True)
                self.assertEqual(self.check(report, 'host.backup')['status'], 'error')
                self.assertNotIn('/etc/shadow', json.dumps(report))

    def test_symlink_backup_files_are_refused(self):
        folder = self.backup()
        target = self.root / 'outside'
        target.write_bytes(b'private')
        path = folder / 'database.sql.gz'
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation is unavailable on this Windows account')
        self.assertEqual(self.check(self.diagnostic.report(verify_backup=True), 'host.backup')['status'], 'error')

    def test_backup_hardlinks_and_non_timestamp_directories_are_not_trusted(self):
        folder = self.backup()
        target = self.root / 'outside'
        target.write_bytes(b'private')
        path = folder / 'database.sql.gz'
        path.unlink()
        os.link(target, path)
        self.assertEqual(self.check(self.diagnostic.report(verify_backup=True), 'host.backup')['status'], 'error')
        path.unlink()
        folder.rename(self.root / 'backups' / 'metadata-only')
        self.assertEqual(self.check(self.diagnostic.report(verify_backup=True), 'host.backup')['status'], 'warning')

    def test_backup_manifest_and_folder_symlink_metadata_are_rejected_without_os_privileges(self):
        folder = self.backup()
        original_lstat = Path.lstat
        for unsafe in (folder, folder / 'SHA256SUMS'):
            def fake_lstat(path, *args, **kwargs):
                values = original_lstat(path, *args, **kwargs)
                if path == unsafe:
                    copied = list(values)
                    copied[0] = 0o120777
                    return os.stat_result(copied)
                return values
            with self.subTest(path=unsafe.name), patch.object(Path, 'lstat', fake_lstat):
                self.assertEqual(self.check(self.diagnostic.report(verify_backup=True), 'host.backup')['status'], 'error')

    def test_missing_backup_and_manifest_are_warning_and_never_reported_verified(self):
        self.assertEqual(self.check(self.diagnostic.report(), 'host.backup')['status'], 'warning')
        folder = self.backup()
        (folder / 'SHA256SUMS').unlink()
        report = self.diagnostic.report(verify_backup=True)
        self.assertEqual(self.check(report, 'host.backup')['status'], 'warning')
        self.assertNotIn('passed SHA-256', json.dumps(report))

    def test_manual_start_only_exact_stopped_container_or_service(self):
        self.runner.containers['spug-db'] = 'exited|none|false|0'
        result = self.diagnostic.start('spug-db')
        self.assertEqual(result['status'], 'ok')
        self.assertIn(('docker', 'start', 'spug-db'), self.runner.calls)
        self.runner.services['spug-worker'] = 'STOPPED'
        result = self.diagnostic.start('spug-worker')
        self.assertEqual(result['status'], 'ok')
        self.assertIn(('docker', 'exec', 'spug', 'supervisorctl', 'start', 'spug-worker'), self.runner.calls)
        self.assertFalse((self.root / '.platform-health.lock').exists())

    def test_start_refuses_running_unknown_auto_retry_and_invalid_targets(self):
        for target in ('spug', 'spug-db', 'spug-worker', 'arbitrary', '--help', 'spug; reboot'):
            self.assertEqual(self.diagnostic.start(target)['status'], 'warning')
        for state in ('BACKOFF', 'STARTING', 'STOPPING', 'UNKNOWN'):
            self.runner.services['spug-worker'] = state
            self.assertEqual(self.diagnostic.start('spug-worker')['status'], 'warning')
        for state in ('dead', 'restarting', 'paused', 'removing', 'unknown'):
            self.runner.containers['spug'] = state + '|none|false|0'
            self.assertEqual(self.diagnostic.start('spug')['status'], 'warning')
        self.assertFalse(any('start' in call for call in self.runner.calls))

    def test_start_checks_fresh_state_inside_lock_and_refuses_existing_lock(self):
        lock = self.root / '.platform-health.lock'
        lock.write_text('busy')
        self.runner.services['spug-worker'] = 'STOPPED'
        self.assertEqual(self.diagnostic.start('spug-worker')['status'], 'warning')
        self.assertEqual(self.runner.calls, [])
        lock.unlink()
        original_runner = self.diagnostic.runner
        def locked_runner(args, timeout=4):
            self.assertTrue(lock.exists())
            return original_runner(args, timeout)
        self.diagnostic.runner = locked_runner
        self.assertEqual(self.diagnostic.start('spug-worker')['status'], 'ok')

    def test_start_timeout_is_not_retried_and_unknown_result_needs_verification(self):
        self.runner.services['spug-worker'] = 'STOPPED'
        original_runner = self.diagnostic.runner
        def timeout_runner(args, timeout=4):
            if 'start' in args:
                self.runner.calls.append(tuple(args))
                raise subprocess.TimeoutExpired('private-token', timeout)
            return original_runner(args, timeout)
        self.diagnostic.runner = timeout_runner
        result = self.diagnostic.start('spug-worker')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(sum('start' in call for call in self.runner.calls), 1)
        self.assertIn('verify', result['advice'])
        self.assertNotIn('private-token', json.dumps(result))

    def test_write_report_is_atomic_private_and_has_fixed_location(self):
        report = self.diagnostic.report()
        target = self.root / 'platform-health.json'
        target.write_text('old')
        self.diagnostic.write_report(report)
        self.assertEqual(json.loads(target.read_text()), report)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith('.platform-health-')], [])
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_cli_options_are_fixed_and_no_report_write_by_default(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = MODULE.main([], diagnostic=self.diagnostic)
        report = json.loads(output.getvalue())
        self.assertIn('checks', report)
        self.assertIn(code, (0, 1, 2))
        self.assertFalse((self.root / 'platform-health.json').exists())
        with patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                MODULE.main(['--start', 'arbitrary'], diagnostic=self.diagnostic)

    def test_cli_does_not_expose_unexpected_probe_exceptions(self):
        output = io.StringIO()
        with patch.object(self.diagnostic, 'report', side_effect=RuntimeError('private-token')):
            with redirect_stdout(output):
                try:
                    code = MODULE.main([], diagnostic=self.diagnostic)
                except RuntimeError:
                    self.fail('The CLI must convert unexpected probe failures to a safe unknown report')
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'unknown')
        self.assertNotIn('private-token', output.getvalue())

    def test_publish_report_is_explicit_and_only_copies_fixed_report_to_running_app(self):
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.main(['--write-report', '--publish-report'], diagnostic=self.diagnostic)
        self.assertIn(('docker', 'cp', str(self.root / 'platform-health.json'),
                       'spug:/data/platform-health.json'), self.runner.calls)
        report = json.loads(output.getvalue())
        self.assertEqual(self.check(report, 'host.publish')['status'], 'ok')
        self.runner.calls.clear()
        self.runner.containers['spug'] = 'exited|none|false|0'
        with redirect_stdout(io.StringIO()):
            MODULE.main(['--write-report', '--publish-report'], diagnostic=self.diagnostic)
        self.assertFalse(any(call[:2] == ('docker', 'cp') for call in self.runner.calls))
        with patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                MODULE.main(['--publish-report'], diagnostic=self.diagnostic)

    def test_failed_report_write_never_publishes_old_report(self):
        (self.root / 'platform-health.json').write_text('private old file')
        output = io.StringIO()
        with patch.object(self.diagnostic, 'write_report', side_effect=OSError('private-token')):
            with redirect_stdout(output):
                MODULE.main(['--write-report', '--publish-report'], diagnostic=self.diagnostic)
        self.assertFalse(any(call[:2] == ('docker', 'cp') for call in self.runner.calls))
        self.assertNotIn('private-token', output.getvalue())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The independent host diagnostic CLI is missing')

    def test_real_runner_times_out_and_bounds_stdout(self):
        import sys
        with self.assertRaises(subprocess.TimeoutExpired):
            MODULE.run_command([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.05)
        with self.assertRaises(ValueError):
            MODULE.run_command([sys.executable, '-c', 'print("x" * 20000)'])
        result = MODULE.run_command([sys.executable, '-c', 'print("running|none|false|0")'])
        self.assertEqual(result.stdout.strip(), 'running|none|false|0')


class HostDiagnosticContractTests(unittest.TestCase):
    def test_independent_host_diagnostic_is_available(self):
        self.assertTrue(SCRIPT.is_file(), 'The independent host diagnostic CLI is missing')


if __name__ == '__main__':
    unittest.main()
