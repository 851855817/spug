import json
import socket
import time
import tempfile
import threading
import shlex
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import TestCase, SimpleTestCase, TransactionTestCase, skipUnlessDBFeature
from django.db import close_old_connections, connections
from concurrent.futures import ThreadPoolExecutor
from django.core.management import call_command, CommandError
from paramiko import AuthenticationException

from apps.account.models import User
from apps.host.models import Group, Host
from . import catalogue, service
from .models import OpsBaseline, OpsRun, OpsTarget
from .runner import ProbeError, parse_probe, run_probe
from .worker import OpsWorker, recover_interrupted


class OpsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='ops-admin', nickname='Ops', is_supper=True,
                                        access_token='a' * 32, token_expired=int(time.time()) + 3600,
                                        last_ip='', last_login='', password_hash='')
        self.client.defaults['HTTP_X_TOKEN'] = self.admin.access_token
        self.hosts = [Host.objects.create(name='Node %s' % i, hostname='host%s.example' % i,
                                        username='root', port=22, pkey='PRIVATE-KEY-%s' % i,
                                        created_by=self.admin) for i in range(3)]

    def create(self, **kwargs):
        data = {'job': 'linux_inspection', 'host_ids': [h.id for h in self.hosts], 'confirmed': True}
        data.update(kwargs)
        return service.create_run(self.admin, data)

    def request(self, method, path, data=None, error=False):
        result = getattr(self.client, method)(path, data=json.dumps(data or {}) if method == 'post' else (data or {}),
                                             content_type='application/json')
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(bool(payload['error']), error, payload)
        return payload['data']

    def test_catalogue_and_superadmin_only(self):
        data = self.request('get', '/ops/catalog/')
        self.assertEqual(len(data['jobs']), 3)
        self.assertEqual(len(data['hosts']), 3)
        self.assertNotIn('PRIVATE', json.dumps(data))
        self.admin.is_supper = False
        self.admin.save()
        for path in ('catalog/', 'runs/', 'baselines/'):
            self.request('get', '/ops/' + path, error=True)

    def test_create_expands_recursive_groups_freezes_and_deduplicates(self):
        parent = Group.objects.create(name='parent')
        child = Group.objects.create(name='child', parent_id=parent.id)
        parent.hosts.add(self.hosts[0])
        child.hosts.add(self.hosts[1])
        run = self.create(host_ids=[self.hosts[0].id], group_ids=[parent.id])
        self.assertEqual(run.state, 'queued')
        self.assertEqual(run.batch_size, 1)
        self.assertEqual(run.failure_threshold, 1)
        self.assertEqual([x.host_id for x in run.targets.all()], [x.id for x in self.hosts[:2]])
        self.hosts[0].name = 'changed'
        self.hosts[0].save()
        self.assertEqual(service.run_view(run)['targets'][0]['host']['name'], 'Node 0')
        self.assertNotIn('PRIVATE', json.dumps(service.run_view(run)))

    def test_create_rejects_unconfirmed_unsafe_parameters_and_limits(self):
        for changes in ({'confirmed': False}, {'job': 'shell'}, {'params': {'command': 'id'}},
                        {'batch_size': 11}, {'batch_size': True}, {'failure_threshold': 0},
                        {'failure_threshold': 2}, {'host_ids': [999]}, {'group_ids': [999]},
                        {'host_ids': []}, {'params': {'php_version': '../../x'}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(**changes)
        extra = [Host(name='extra', hostname='extra', username='root', pkey='KEY', created_by=self.admin)
                 for _ in range(48)]
        Host.objects.bulk_create(extra)
        with self.assertRaises(ValueError):
            self.create(host_ids=list(Host.objects.values_list('id', flat=True)))

    def test_success_pauses_after_batch_and_explicit_continue_completes(self):
        run = self.create()
        worker = OpsWorker(probe=lambda *a: parse_probe('linux_inspection', {'values': {'cpu_count': 4}}))
        self.assertTrue(worker.run_once())
        run.refresh_from_db()
        self.assertEqual(run.state, 'paused')
        self.assertEqual(run.targets.filter(state='success').count(), 1)
        self.assertFalse(worker.run_once())
        service.continue_run(self.admin, run.id, {})
        worker.run_once()
        run.refresh_from_db()
        self.assertEqual(run.state, 'paused')
        service.continue_run(self.admin, run.id, {})
        worker.run_once()
        run.refresh_from_db()
        self.assertEqual(run.state, 'completed')
        self.assertEqual(run.targets.filter(state='success').count(), 3)
        self.assertEqual(run.audit.filter(event='continue').count(), 2)

    def test_threshold_trips_mid_batch_and_requires_acknowledgment(self):
        run = self.create(batch_size=3, failure_threshold=1)
        def failure(*args):
            raise ProbeError('timeout')
        worker = OpsWorker(probe=failure)
        worker.run_once()
        run.refresh_from_db()
        self.assertEqual(run.state, 'tripped')
        self.assertEqual(run.targets.filter(state='pending').count(), 2)
        with self.assertRaises(ValueError):
            service.continue_run(self.admin, run.id, {})
        service.continue_run(self.admin, run.id, {'acknowledge': True})
        run.refresh_from_db()
        self.assertEqual(run.state, 'queued')
        self.assertEqual(run.batch_failures, 0)

    def test_batch_has_bounded_size_and_failure_counter_resets(self):
        run = self.create(batch_size=2, failure_threshold=2)
        worker = OpsWorker(probe=lambda *a: parse_probe('linux_inspection', {'values': {'cpu_count': 2}}))
        worker.run_once()
        run.refresh_from_db()
        self.assertEqual(run.state, 'running')
        worker.run_once()
        run.refresh_from_db()
        self.assertEqual(run.state, 'paused')
        self.assertEqual(run.targets.filter(state='pending').count(), 1)

    def test_revalidate_identity_and_creator_permission_before_dispatch(self):
        for kind in ('identity', 'permission'):
            with self.subTest(kind=kind):
                run = self.create(host_ids=[self.hosts[0].id])
                if kind == 'identity':
                    self.hosts[0].hostname = 'changed.example'
                    self.hosts[0].save()
                else:
                    self.admin.is_supper = False
                    self.admin.save()
                probe = Mock()
                OpsWorker(probe=probe).run_once()
                probe.assert_not_called()
                target = run.targets.get()
                self.assertEqual(target.state, 'failed')
                self.assertEqual(target.category, 'host_changed' if kind == 'identity' else 'permission_denied')

    def test_cancel_prevents_future_dispatch_and_preserves_inflight(self):
        run = self.create()
        worker = OpsWorker(probe=Mock())
        target_id = worker.claim_next()
        service.cancel_run(self.admin, run.id)
        target = OpsTarget.objects.get(id=target_id)
        self.assertEqual(target.state, 'running')
        self.assertEqual(run.targets.filter(state='cancelled').count(), 2)
        self.assertFalse(worker.run_once())
        worker.finish(target_id, checks=[], category='check_error')
        run.refresh_from_db()
        self.assertEqual(run.state, 'cancelled')

    def test_claim_is_cas_and_recovery_never_retries_inflight(self):
        run = self.create()
        worker = OpsWorker()
        target_id = worker.claim_next()
        self.assertIsNotNone(target_id)
        self.assertIsNone(OpsWorker().claim_next())
        recover_interrupted()
        target = OpsTarget.objects.get(id=target_id)
        run.refresh_from_db()
        self.assertEqual(target.state, 'unknown')
        self.assertEqual(target.category, 'interrupted')
        self.assertEqual(run.state, 'tripped')
        self.assertFalse(worker.run_once())
        service.continue_run(self.admin, run.id, {'acknowledge': True})
        self.assertNotEqual(worker.claim_next(), target_id)

    def test_baseline_immutable_same_version_and_diff_missing_unknown(self):
        run = self.create()
        checks = parse_probe('linux_inspection', {'values': {'cpu_count': 4, 'root_used_percent': 10}})
        first = run.targets.first()
        first.state, first.checks = 'success', checks
        first.save()
        baseline = service.create_baseline(self.admin, {'run_id': run.id, 'target_id': first.id, 'name': 'Stable'})
        first.checks = parse_probe('linux_inspection', {'values': {'cpu_count': 8}})
        first.save()
        diff = service.diff_run(run, baseline.id)
        self.assertEqual(diff['items'][0]['changes'][0]['state'], 'unknown')
        changes = {x['key']: x for x in diff['items'][0]['changes']}
        self.assertEqual(changes['cpu_count']['state'], 'changed')
        self.assertEqual(changes['root_used_percent']['state'], 'missing')
        self.assertTrue(all(x['state'] == 'unknown' for x in diff['items'][1]['changes']))
        baseline.name = 'Changed'
        with self.assertRaises(ValueError):
            baseline.save()
        run.version = '2'
        run.save()
        with self.assertRaises(ValueError):
            service.diff_run(run, baseline.id)
        with self.assertRaises(ValueError):
            service.create_baseline(self.admin, {'run_id': run.id, 'target_id': run.targets.last().id, 'name': 'bad'})

    def test_api_run_report_continue_cancel_and_baseline_contract(self):
        run = self.request('post', '/ops/runs/', {'job': 'php_environment', 'host_ids': [self.hosts[0].id],
                                                'params': {'php_version': '74'}, 'confirmed': True})
        self.assertEqual(run['state'], 'queued')
        self.assertEqual(self.request('get', '/ops/runs/')['total'], 1)
        self.assertEqual(self.request('get', '/ops/runs/%s/report/' % run['id'])['id'], run['id'])
        self.request('post', '/ops/runs/%s/continue/' % run['id'], error=True)
        self.request('post', '/ops/runs/%s/cancel/' % run['id'])
        self.assertEqual(self.request('get', '/ops/runs/%s/' % run['id'])['state'], 'cancelled')

    def test_run_list_supports_filters_and_page_pagination(self):
        self.create(job='php_environment')
        second = self.create()
        result = self.request('get', '/ops/runs/', {'job': 'linux_inspection', 'state': 'queued',
                                                   'page': 1, 'page_size': 1})
        self.assertEqual(result['total'], 1)
        self.assertEqual([x['id'] for x in result['items']], [second.id])
        self.assertEqual(self.request('get', '/ops/runs/', {'page': 2, 'page_size': 1})['items'][0]['job'],
                         'php_environment')

    def test_baseline_list_paginates_filters_and_searches_immutable_history(self):
        run = self.create(job='php_environment', host_ids=[self.hosts[0].id])
        target = run.targets.get()
        target.state = 'success'
        target.checks = parse_probe('php_environment', {'values': {'php_version': '7.4.33'}})
        target.save()
        for name in ('Stable A', 'Stable B', 'Other'):
            service.create_baseline(self.admin, {'run_id': run.id, 'target_id': target.id, 'name': name})
        result = self.request('get', '/ops/baselines/', {'job': 'php_environment', 'version': '1',
                                                       'php_version': '74', 'q': 'Stable',
                                                       'page': 2, 'page_size': 1})
        self.assertEqual(result['total'], 2)
        self.assertEqual([x['name'] for x in result['items']], ['Stable A'])
        self.assertEqual(self.request('get', '/ops/baselines/', {'php_version': '83'})['total'], 0)
        self.assertEqual(self.request('get', '/ops/baselines/', {'job': 'web_environment'})['total'], 0)
        self.assertEqual(self.request('get', '/ops/baselines/', {'version': '2'})['total'], 0)

    def test_sensitive_error_never_reaches_worker_report(self):
        run = self.create(host_ids=[self.hosts[0].id])
        def fail(*args):
            raise RuntimeError('PRIVATE CONFIG token=secret')
        OpsWorker(probe=fail).run_once()
        run.refresh_from_db()
        report = self.request('get', '/ops/runs/%s/' % run.id)
        self.assertEqual(report['targets'][0]['category'], 'check_error')
        self.assertNotIn('secret', json.dumps(report))
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_post_dispatch_disconnect_is_unknown_and_trips_without_retry(self):
        run = self.create(batch_size=2)
        def disconnect(*args):
            raise ProbeError('connection', uncertain=True)
        worker = OpsWorker(probe=disconnect)
        worker.run_once()
        run.refresh_from_db()
        self.assertEqual(run.state, 'tripped')
        self.assertEqual(run.targets.first().state, 'unknown')
        self.assertEqual(run.targets.first().category, 'connection')
        self.assertEqual(service.run_view(run)['counts']['unknown'], 1)
        self.assertFalse(worker.run_once())

    def test_management_worker_uses_dedicated_lock_and_does_not_steal_it(self):
        from apps.baota.management.commands.runpanelworker import ProcessLock
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'ops.lock')
            with ProcessLock(path), patch('apps.ops.management.commands.runopsworker.OpsWorker') as worker:
                with self.assertRaises(CommandError):
                    call_command('runopsworker', once=True, lock_file=path)
                worker.return_value.run_once.assert_not_called()
            with patch('apps.ops.management.commands.runopsworker.OpsWorker') as worker:
                call_command('runopsworker', once=True, lock_file=path)
                worker.return_value.run_once.assert_called_once()

    def test_ssh_credentials_change_blocks_dispatch_and_deleted_host_is_preserved(self):
        run = self.create(host_ids=[self.hosts[0].id, self.hosts[1].id])
        self.hosts[0].pkey = 'new-key'
        self.hosts[0].save()
        probe = Mock()
        worker = OpsWorker(probe=probe)
        worker.run_once()
        self.assertEqual(run.targets.first().category, 'host_changed')
        self.hosts[1].delete()
        service.continue_run(self.admin, run.id, {'acknowledge': True})
        worker.run_once()
        probe.assert_not_called()
        self.assertEqual(run.targets.last().host_snapshot['name'], 'Node 1')
        self.assertEqual(run.targets.last().category, 'host_changed')


class ProbeTests(SimpleTestCase):
    def test_php_probe_treats_stderr_as_enabled_and_invalid_flags_as_unknown(self):
        script = shlex.split(catalogue.command('php_environment', {'php_version': '74'}))[8]
        self.assertIn("$k==='display_errors'", script)
        self.assertIn("['stderr','stdout','2']", script)
        self.assertIn('FILTER_NULL_ON_FAILURE', script)
        checks = parse_probe('php_environment', {'values': {'display_errors': True, 'expose_php': False}})
        by_key = {x['key']: x for x in checks}
        self.assertEqual(by_key['display_errors']['status'], 'warning')
        self.assertEqual(by_key['expose_php']['status'], 'ok')
        self.assertEqual(by_key['allow_url_include']['status'], 'unknown')

    def test_fixed_catalogue_rejects_commands_paths_and_php_injection(self):
        self.assertEqual(len(catalogue.jobs()), 3)
        self.assertNotIn('PRIVATE', catalogue.command('linux_inspection', {}))
        for params in ({'php_version': '74;id'}, {'path': '/etc/shadow'}, {'template': 'custom'}):
            with self.assertRaises(ValueError):
                catalogue.command('php_environment', params)

    def test_parser_whitelists_types_and_marks_missing_data_unknown(self):
        checks = parse_probe('linux_inspection', {'values': {'cpu_count': 4, 'root_used_percent': 99,
                                                           'env': 'SECRET', 'os_version': 'token=SECRET'}})
        by_key = {x['key']: x for x in checks}
        self.assertEqual(by_key['cpu_count']['value'], 4)
        self.assertEqual(by_key['root_used_percent']['status'], 'warning')
        self.assertEqual(by_key['os_version']['status'], 'unknown')
        self.assertNotIn('SECRET', json.dumps(checks))
        self.assertEqual(by_key['load_1']['status'], 'unknown')
        with self.assertRaises(ProbeError):
            parse_probe('linux_inspection', {'values': {'cpu_count': 'SECRET'}})

    def test_ssh_errors_have_safe_categories_and_no_raw_exception(self):
        host = Mock(hostname='host.example', port=22, username='root', private_key='PRIVATE')
        for exception, category in ((AuthenticationException('SECRET'), 'ssh_auth'),
                                    (socket.timeout('SECRET'), 'timeout'), (OSError('SECRET'), 'connection')):
            with self.subTest(category=category), patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                    patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                    patch('apps.ops.runner.SSHClient') as factory:
                factory.return_value.connect.side_effect = exception
                with self.assertRaises(ProbeError) as raised:
                    run_probe(host, 'linux_inspection', {})
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn('SECRET', str(raised.exception))

    def test_ssh_output_cap_and_fixed_probe_command(self):
        host = Mock(hostname='host.example', port=22, username='root', private_key='PRIVATE')
        with patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                patch('apps.ops.runner.SSHClient') as factory:
            channel = factory.return_value.get_transport.return_value.open_session.return_value
            channel.recv_ready.return_value = True
            channel.recv.return_value = b'x' * 4096
            with self.assertRaises(ProbeError) as raised:
                run_probe(host, 'linux_inspection', {})
            self.assertEqual(raised.exception.category, 'check_error')
            self.assertLessEqual(channel.recv.call_count, 17)
            self.assertTrue(channel.close.called)
            self.assertTrue(factory.return_value.close.called)
            self.assertLessEqual(factory.return_value.connect.call_args.kwargs['timeout'], 10)

    def test_eof_without_exit_status_is_bounded_by_overall_deadline(self):
        host = Mock(hostname='host.example', port=22, username='root', private_key='PRIVATE')
        with patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                patch('apps.ops.runner.time.monotonic', side_effect=range(200)), \
                patch('apps.ops.runner.time.sleep'), patch('apps.ops.runner.SSHClient') as factory:
            channel = factory.return_value.get_transport.return_value.open_session.return_value
            channel.recv_ready.return_value = True
            channel.recv.return_value = b''
            channel.exit_status_ready.return_value = False
            with self.assertRaises(ProbeError) as raised:
                run_probe(host, 'linux_inspection', {})
            self.assertEqual(raised.exception.category, 'timeout')
            channel.recv_exit_status.assert_not_called()

    def test_deadline_watchdog_closes_blocked_exec_request(self):
        host = Mock(hostname='host.example', port=22, username='root', private_key='PRIVATE')
        released = threading.Event()
        with patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                patch('apps.ops.runner.TOTAL_TIMEOUT', 0.03), patch('apps.ops.runner.SSHClient') as factory:
            channel = factory.return_value.get_transport.return_value.open_session.return_value
            channel.exec_command.side_effect = lambda *_: released.wait(0.5)
            channel.close.side_effect = released.set
            started = time.monotonic()
            with self.assertRaises(ProbeError) as raised:
                run_probe(host, 'linux_inspection', {})
            self.assertEqual(raised.exception.category, 'timeout')
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertTrue(raised.exception.uncertain)
            self.assertTrue(factory.return_value.close.called)

    def test_cleanup_errors_do_not_expose_secrets_or_replace_safe_category(self):
        host = Mock(hostname='host.example', port=22, username='root', private_key='PRIVATE')
        with patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                patch('apps.ops.runner.SSHClient') as factory:
            factory.return_value.connect.side_effect = AuthenticationException('SECRET')
            factory.return_value.close.side_effect = RuntimeError('PRIVATE SECRET')
            with self.assertRaises(ProbeError) as raised:
                run_probe(host, 'linux_inspection', {})
            self.assertEqual(raised.exception.category, 'ssh_auth')

    def test_dependency_and_valid_output_are_parsed_without_raw_output(self):
        host = Mock(hostname='host.example', port=22, username='root', private_key='PRIVATE')
        with patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                patch('apps.ops.runner.SSHClient') as factory:
            channel = factory.return_value.get_transport.return_value.open_session.return_value
            channel.recv_ready.side_effect = [True, False]
            channel.recv.return_value = b'{"values":{"cpu_count":4},"raw":"SECRET"}'
            channel.exit_status_ready.return_value = True
            channel.recv_exit_status.return_value = 0
            checks = run_probe(host, 'linux_inspection', {})
            self.assertNotIn('SECRET', json.dumps(checks))
            channel.recv_ready.side_effect = None
            channel.recv_ready.return_value = False
            channel.recv_exit_status.return_value = 42
            with self.assertRaises(ProbeError) as raised:
                run_probe(host, 'linux_inspection', {})
            self.assertEqual(raised.exception.category, 'dependency')


class OpsConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_mysql_claim_serializes_two_coordinators(self):
        admin = User.objects.create(username='concurrency-admin', nickname='Ops', is_supper=True,
                                    access_token='c' * 32, token_expired=int(time.time()) + 3600,
                                    last_ip='', last_login='', password_hash='')
        hosts = [Host.objects.create(name='concurrent', hostname='192.0.2.%s' % i,
                                     port=22, username='root', pkey='KEY', created_by=admin)
                 for i in (1, 2)]
        run = service.create_run(admin, {'job': 'linux_inspection', 'host_ids': [x.id for x in hosts],
                                         'batch_size': 2, 'confirmed': True})
        barrier = threading.Barrier(2)
        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return OpsWorker().claim_next()
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(claim), pool.submit(claim)
            results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(run.targets.filter(state='running').count(), 1)
        self.assertEqual(run.targets.filter(state='pending').count(), 1)
