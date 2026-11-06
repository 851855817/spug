"""Operational runs and baselines use the same account resource boundary."""
import json
from unittest.mock import Mock, patch
from django.test import TestCase, RequestFactory
from apps.account.models import User, Role
from apps.host.models import Group, Host
from apps.ops import service, views
from apps.ops.models import OpsRun
from apps.ops.worker import OpsWorker


class OpsResourceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='admin', is_supper=True)
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b')
        self.role = Role.objects.create(name='ops', page_perms={'ops': {'job': ['view', 'create', 'continue', 'cancel', 'baseline']}})
        self.a.roles.add(self.role)
        self.host = Host.objects.create(name='own', hostname='192.0.2.1', username='root', pkey='fixture', created_by=self.a)
        self.other = Host.objects.create(name='hidden', hostname='192.0.2.2', username='root', pkey='fixture', created_by=self.b)
        self.factory = RequestFactory()

    def data(self, *hosts):
        from apps.ops import catalogue
        return {'job': catalogue.jobs()[0]['code'], 'host_ids': [h.pk for h in hosts], 'confirmed': True}

    def fixture_run(self, owner, *hosts):
        run = service.create_run(self.admin, self.data(*hosts))
        run.created_by = owner
        run.save()
        return run

    def request(self, view, **kwargs):
        request = self.factory.get('/')
        request.user = self.a
        response = view.as_view()(request, **kwargs)
        return json.loads(response.content)

    def test_normal_account_can_create_own_authorized_inspection(self):
        run = service.create_run(self.a, self.data(self.host))
        self.assertEqual(run.created_by_id, self.a.pk)

    def test_mixed_explicit_targets_are_rejected_whole(self):
        with self.assertRaises(ValueError):
            service.create_run(self.a, self.data(self.host, self.other))
        self.assertEqual(OpsRun.objects.count(), 0)

    def test_lists_and_counts_only_include_own_fully_authorized_runs(self):
        own = self.fixture_run(self.a, self.host)
        self.fixture_run(self.b, self.other)
        self.fixture_run(self.a, self.host, self.other)
        data = self.request(views.RunsView)
        self.assertFalse(data['error'])
        self.assertEqual(data['data']['total'], 1)
        self.assertEqual([r['id'] for r in data['data']['items']], [own.pk])

    def test_cannot_read_another_accounts_run_by_id(self):
        other = self.fixture_run(self.b, self.other)
        self.assertTrue(self.request(views.RunView, run_id=other.pk)['error'])

    def test_worker_accepts_authorized_normal_account(self):
        self.fixture_run(self.a, self.host)
        probe = Mock(return_value=[])
        self.assertTrue(OpsWorker(probe=probe).run_once())
        probe.assert_called_once()

    def test_view_does_not_implicitly_allow_cancel(self):
        run = self.fixture_run(self.a, self.host)
        self.role.page_perms = {'ops': {'job': ['view']}}
        self.role.save()
        with self.assertRaises(ValueError):
            service.cancel_run(self.a, run.pk)

    def test_revoked_second_target_prevents_dispatch_to_first(self):
        self.fixture_run(self.a, self.host, self.other)
        probe = Mock()
        OpsWorker(probe=probe).run_once()
        probe.assert_not_called()

    def test_ssh_connection_cannot_send_command_after_revoke(self):
        from apps.ops.runner import run_probe
        run = self.fixture_run(self.a, self.host)
        self.host.resource_guard = lambda: service.authorized_run(self.a, run, 'create', manage=True)
        with patch('apps.ops.runner.SSH.load_pkey', return_value=Mock()), \
                patch('apps.ops.runner._connect_socket', return_value=Mock()), \
                patch('apps.ops.runner.SSHClient') as factory:
            factory.return_value.connect.side_effect = lambda **kwargs: self.a.roles.clear()
            channel = factory.return_value.get_transport.return_value.open_session.return_value
            channel.recv_ready.return_value = False
            channel.exit_status_ready.return_value = True
            channel.recv_exit_status.return_value = 1
            with self.assertRaises(PermissionError):
                run_probe(self.host, 'linux_inspection', {})
            channel.exec_command.assert_not_called()

    def test_run_and_baseline_views_hide_unassigned_group_metadata(self):
        group = Group.objects.create(name='private-team')
        self.host.groups.add(group)
        run = self.fixture_run(self.a, self.host)
        target = run.targets.first()
        target.state = 'success'
        target.checks = [{'key': 'test', 'status': 'normal', 'value': 'fixture'}]
        target.save()
        baseline = service.create_baseline(self.a, {'run_id': run.pk, 'target_id': target.pk, 'name': 'fixture'})
        self.assertEqual(service.baseline_view(baseline, user=self.a)['host']['group_ids'], [])
        self.assertEqual(service.diff_run(run, baseline.pk, user=self.a)['items'][0]['host']['group_ids'], [])

    def test_baseline_comparison_rechecks_both_resource_scopes_in_service(self):
        own = self.fixture_run(self.a, self.host)
        other = self.fixture_run(self.b, self.other)
        target = other.targets.first()
        target.state = 'success'
        target.checks = [{'key': 'test', 'status': 'normal', 'value': 'fixture'}]
        target.save()
        baseline = service.create_baseline(self.admin, {'run_id': other.pk, 'target_id': target.pk, 'name': 'other'})
        with self.assertRaises(ValueError):
            service.diff_run(own, baseline.pk, user=self.a)

    def test_dns_completion_after_revocation_cannot_start_tcp(self):
        import socket
        # The resolver daemon changes only fixture state; the account callback
        # remains on the caller thread, as it must in the real implementation.
        from apps.ops.runner import _connect_socket
        active = [True]
        def authorize():
            if not active[0]:
                raise PermissionError('revoked')
        def resolver(*args, **kwargs):
            active[0] = False
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('192.0.2.1', 22))]
        import time
        with patch('apps.ops.runner.socket.getaddrinfo', side_effect=resolver), \
                patch('apps.ops.runner.socket.socket') as connect:
            with self.assertRaises(PermissionError):
                _connect_socket('fixture.example', 22, time.monotonic() + 5, authorize=authorize)
        connect.assert_not_called()
