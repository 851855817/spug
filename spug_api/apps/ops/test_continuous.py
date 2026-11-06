"""Continuous inspections use isolated records and simulated probes only."""
from unittest import mock
import socket
from django.test import TestCase, SimpleTestCase
from apps.account.models import User
from apps.host.models import Host
from . import service
from .runner import ProbeError, run_probe
from .worker import OpsWorker, recover_interrupted


class ContinuousTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='continuous', nickname='test', is_supper=True,
            last_ip='', last_login='', password_hash='')
        self.hosts = [Host.objects.create(name='Node %s' % i, hostname='192.0.2.%s' % (i + 1),
            username='root', port=22, pkey='NOT-A-REAL-KEY', created_by=self.admin) for i in range(3)]

    def create(self, **changes):
        return service.create_run(self.admin, dict(job='linux_inspection', host_ids=[h.id for h in self.hosts],
            confirmed=True, execution_mode='continuous', **changes))

    def test_failures_and_unknown_results_continue_without_repeating_targets(self):
        run = self.create()
        probe = mock.Mock(side_effect=[ProbeError('ssh_auth'), ProbeError('timeout', uncertain=True),
            [{'key': 'cpu_count', 'label': 'CPU', 'status': 'ok', 'value': 4, 'summary': '已采集'}]])
        worker = OpsWorker(probe=probe)
        for _ in self.hosts: self.assertTrue(worker.run_once())
        run.refresh_from_db()
        self.assertEqual(run.state, 'completed')
        self.assertEqual(list(run.targets.values_list('state', flat=True)), ['failed', 'unknown', 'success'])
        self.assertFalse(worker.run_once())
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(service.run_view(run)['execution_mode'], 'continuous')
        self.assertEqual(run.audit.filter(event='continue').count(), 0)

    def test_recovery_continues_pending_without_resending_inflight(self):
        run = self.create(); worker = OpsWorker(probe=mock.Mock(return_value=[]))
        interrupted = worker.claim_next()
        recover_interrupted()
        run.refresh_from_db(); self.assertEqual(run.state, 'queued')
        self.assertTrue(worker.run_once()); self.assertTrue(worker.run_once())
        run.refresh_from_db(); self.assertEqual(run.state, 'completed')
        self.assertEqual(run.targets.get(pk=interrupted).state, 'unknown')
        self.assertEqual(worker.probe.call_count, 2)

    def test_cancellation_and_revoked_permission_do_not_dispatch(self):
        run = self.create(); probe = mock.Mock(); worker = OpsWorker(probe=probe)
        service.cancel_run(self.admin, run.id)
        self.assertFalse(worker.run_once()); probe.assert_not_called()
        run = self.create(); self.admin.is_supper = False; self.admin.save()
        for _ in self.hosts: self.assertTrue(worker.run_once())
        run.refresh_from_db(); self.assertEqual(run.state, 'completed'); probe.assert_not_called()
        self.assertTrue(all(t.category == 'permission_denied' for t in run.targets.all()))

    def test_policy_validation_keeps_old_manual_calls(self):
        for changes in ({'batch_size': 1}, {'failure_threshold': 1}):
            with self.assertRaises(ValueError): self.create(**changes)
        with self.assertRaises(ValueError):
            service.create_run(self.admin, {'job': 'linux_inspection', 'host_ids': [self.hosts[0].id],
                'execution_mode': 'arbitrary', 'confirmed': True})
        run = service.create_run(self.admin, {'job': 'linux_inspection', 'host_ids': [h.id for h in self.hosts], 'confirmed': True})
        worker = OpsWorker(probe=mock.Mock(return_value=[])); worker.run_once()
        run.refresh_from_db(); self.assertEqual(run.state, 'paused')
        self.assertEqual(service.run_view(run)['execution_mode'], 'manual')

    def test_worker_keeps_specific_safe_diagnostic(self):
        run = self.create()
        worker = OpsWorker(probe=mock.Mock(side_effect=ProbeError('dependency', reason='php_missing', php_version='74', exit_code=42)))
        worker.run_once(); target = run.targets.first()
        self.assertIn('/www/server/php/74/bin/php', target.message)
        self.assertIn('42', target.message)

    def test_fifty_failures_exhaust_all_targets_without_tripping(self):
        for i in range(47):
            self.hosts.append(Host.objects.create(name='extra %s' % i, hostname='198.51.100.%s' % (i + 1),
                username='root', port=22, pkey='NOT-A-REAL-KEY', created_by=self.admin))
        run = self.create(); worker = OpsWorker(probe=mock.Mock(side_effect=ProbeError('connection')))
        for _ in self.hosts: self.assertTrue(worker.run_once())
        run.refresh_from_db(); self.assertEqual(run.state, 'completed')
        self.assertEqual(run.targets.filter(state='failed').count(), 50)
        self.assertEqual(worker.probe.call_count, 50)

    def test_changed_identity_is_skipped_and_other_hosts_continue(self):
        run = self.create(); host = self.hosts[0]; host.port = 2222; host.save()
        worker = OpsWorker(probe=mock.Mock(return_value=[]))
        for _ in self.hosts: self.assertTrue(worker.run_once())
        run.refresh_from_db(); self.assertEqual(run.state, 'completed')
        self.assertEqual(run.targets.first().category, 'host_changed')
        self.assertEqual(worker.probe.call_count, 2)

    def test_recovery_on_last_target_completes_without_resending(self):
        run = service.create_run(self.admin, {'job': 'linux_inspection', 'host_ids': [self.hosts[0].id],
            'execution_mode': 'continuous', 'confirmed': True})
        worker = OpsWorker(probe=mock.Mock()); worker.claim_next(); recover_interrupted()
        run.refresh_from_db(); self.assertEqual(run.state, 'completed')
        self.assertFalse(worker.run_once()); worker.probe.assert_not_called()


class DetailedProbeTests(SimpleTestCase):
    def probe_error(self, exit_code=0, output=b'not-json SECRET-PASSWORD', job='linux_inspection', params=None):
        host = mock.Mock(hostname='192.0.2.1', port=22, username='root', private_key='PRIVATE-SECRET')
        with mock.patch('apps.ops.runner.SSH.load_pkey', return_value=mock.Mock()), \
             mock.patch('apps.ops.runner._connect_socket', return_value=mock.Mock()), \
             mock.patch('apps.ops.runner.SSHClient') as factory:
            channel = factory.return_value.get_transport.return_value.open_session.return_value
            channel.recv_ready.side_effect = [True, False]; channel.recv.return_value = output
            channel.exit_status_ready.return_value = True; channel.recv_exit_status.return_value = exit_code
            with self.assertRaises(ProbeError) as raised: run_probe(host, job, params or {})
            self.assertNotIn('SECRET', str(raised.exception))
            return str(raised.exception)

    def test_exit_code_and_php_path_are_specific_without_remote_output(self):
        self.assertIn('退出码 23', self.probe_error(exit_code=23))
        message = self.probe_error(exit_code=42, job='php_environment', params={'php_version': '74'})
        self.assertIn('/www/server/php/74/bin/php', message)
        self.assertIn('python3', self.probe_error(exit_code=127))

    def test_php_exit_127_does_not_suggest_unrelated_python_dependency(self):
        message = self.probe_error(exit_code=127, job='php_environment', params={'php_version': '74'})
        self.assertNotIn('python3', message)
        self.assertIn('127', message)
        self.assertIn('PHP', message)

    def test_stage_timeout_does_not_claim_sixty_second_deadline(self):
        host = mock.Mock(hostname='192.0.2.1', port=22, username='root', private_key='PRIVATE-SECRET')
        with mock.patch('apps.ops.runner.SSH.load_pkey', return_value=mock.Mock()), \
             mock.patch('apps.ops.runner._connect_socket', return_value=mock.Mock()), \
             mock.patch('apps.ops.runner.SSHClient') as factory:
            factory.return_value.connect.side_effect = socket.timeout('SECRET')
            with self.assertRaises(ProbeError) as raised: run_probe(host, 'linux_inspection', {})
            self.assertIn('握手', str(raised.exception))
            self.assertNotIn('总时限', str(raised.exception))
            self.assertNotIn('SECRET', str(raised.exception))

    def test_invalid_json_and_missing_measurements_are_distinct(self):
        self.assertIn('JSON', self.probe_error())
        self.assertIn('有效指标', self.probe_error(output=b'{"values":{}}'))

    def test_untrusted_diagnostic_values_are_not_saved(self):
        message = str(ProbeError('dependency', reason='php_missing', php_version='SECRET', exit_code='SECRET'))
        self.assertNotIn('SECRET', message)
