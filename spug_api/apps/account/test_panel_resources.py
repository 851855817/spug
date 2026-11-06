"""Panel resources, independent actions and durable workers never share scope."""
import json
import time
from contextlib import contextmanager
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from django.test import TestCase, RequestFactory

from apps.account.models import User, Role, ResourceGroupOwner, ResourceGrant, AccountResourcePolicy
from apps.baota.models import Panel, PanelGroup, PanelTask, PanelTaskTarget
from apps.baota import task_service as s, views, task_views, connection_views, login_views
from apps.baota.worker import PanelWorker
from apps.schedule import panel_tasks
from apps.schedule.models import Task
from apps.schedule import views as schedule_views, executors
from apps.schedule.scheduler import Scheduler
from apps.schedule.models import History
from apps.host.models import Host


class PanelResourceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='admin', is_supper=True)
        self.a = User.objects.create(username='a', access_token='a'*32, token_expired=int(time.time())+3600)
        self.b = User.objects.create(username='b')
        self.role = Role.objects.create(name='panels', page_perms={
            'baota': {'panel': ['view', 'add', 'edit', 'del', 'login', 'test'], 'group': ['manage']},
            'exec': {'panel': ['view', 'create', 'execute', 'cancel', 'manage'], 'panel_action': ['site_basedir', 'file_upload']},
            'schedule': {'schedule': ['view', 'add', 'edit', 'enable', 'run', 'del']}})
        self.a.roles.add(self.role)
        self.group = PanelGroup.objects.create(name='a-private')
        self.other_group = PanelGroup.objects.create(name='b-private')
        ResourceGroupOwner.objects.create(kind='panel', group_id=self.group.pk, owner=self.a)
        ResourceGroupOwner.objects.create(kind='panel', group_id=self.other_group.pk, owner=self.b)
        self.panel = self.make_panel(self.a, self.group)
        self.other = self.make_panel(self.b, self.other_group)
        self.factory = RequestFactory()

    def make_panel(self, owner, group):
        return Panel.objects.create(name=owner.username, group=group, created_by=owner,
            url='https://example.invalid', username='panel', password_ciphertext='fixture',
            api_base_url='https://example.invalid', api_key_ciphertext='fixture',
            api_checked_at='2026-11-01 12:00:00', api_version='13.1.0')

    def request(self, view, method='get', data=None, user=None, **kwargs):
        data = data or {}
        request = getattr(self.factory, method)('/', data=json.dumps(data) if method in ('post', 'patch') else data,
                                                  content_type='application/json')
        if method == 'delete':
            request = self.factory.delete('/?' + urlencode(data))
        request.user = user or self.a
        return json.loads(view.as_view()(request, **kwargs).content)

    def fixture_task(self, *panels, owner=None, operation='site_basedir', state='prepared'):
        task = PanelTask.objects.create(owner=owner or self.a, operation=operation, state=state,
            params_by_panel={str(p.pk): {'sites': [1], 'enabled': True} for p in panels})
        for panel in panels:
            PanelTaskTarget.objects.create(task=task, panel=panel, panel_snapshot=s.safe_panel(panel),
                params={'sites': [1], 'enabled': True}, state=state,
                children=[{'snapshot': {'key': '1', 'label': 'site', 'target': {'id': 1}, 'before': {},
                    'plan': {}, 'state': 'ready'}, 'state': 'ready', 'intents': [], 'result': {}}])
        return task

    def test_list_only_shows_resources_in_scope(self):
        result = self.request(views.PanelView)
        self.assertEqual([x['id'] for x in result['data']], [self.panel.pk])

    def test_groups_do_not_leak_other_owners(self):
        self.assertEqual([g['id'] for g in self.request(views.GroupView)['data']], [self.group.pk])

    def test_group_flags_distinguish_owned_from_visible_parent(self):
        self.group.parent = self.other_group
        self.group.save(update_fields=['parent'])
        rows = self.request(views.GroupView)['data']
        self.assertTrue(next(g for g in rows if g['id'] == self.group.pk)['can_manage'])
        self.assertFalse(next(g for g in rows if g['id'] == self.other_group.pk)['can_manage'])

    def test_shared_panel_manager_can_edit_without_disclosing_or_moving_hidden_group(self):
        from apps.account import access
        access.set_policy(self.admin, self.a, 3)
        access.assign(self.admin, self.a, 'panel', [self.other.pk], 'manage')
        data = {'id': self.other.pk, 'name': 'managed', 'url': self.other.url, 'username': self.other.username}
        for extra in ({}, {'group_id': None}):
            result = self.request(views.PanelView, 'post', dict(data, **extra))
            self.assertFalse(result['error'], result)
            self.assertIsNone(result['data']['group_id'])
            self.other.refresh_from_db()
            self.assertEqual(self.other.group_id, self.other_group.pk)
        result = self.request(views.PanelView, 'post', dict(data, group_id=self.group.pk))
        self.assertTrue(result['error'])
        self.other.refresh_from_db()
        self.assertEqual(self.other.group_id, self.other_group.pk)

    def test_connection_to_other_panel_is_rejected_before_client_creation(self):
        with patch('apps.baota.connection_views.PanelClient') as client:
            self.assertTrue(self.request(connection_views.ConnectionView, 'post', {'id': self.other.pk})['error'])
        client.assert_not_called()

    def test_test_permission_is_independent_of_panel_edit(self):
        for actions, allowed in ((['view', 'test'], True), (['view', 'edit'], False)):
            self.role.page_perms['baota']['panel'] = actions
            self.role.save()
            with patch('apps.baota.connection_views.PanelClient') as client, \
                    patch('apps.baota.connection_views.inspect_certificate', return_value={'fingerprint': 'a' * 64}) as inspect:
                client.return_value.__enter__.return_value.request.return_value = {'system': 'Ubuntu', 'version': '13.1.0'}
                connection = self.request(connection_views.ConnectionView, 'post', {'id': self.panel.pk})
                certificate = self.request(connection_views.CertificateView, 'post', {'id': self.panel.pk})
                self.assertEqual(not bool(connection['error']), allowed, connection)
                self.assertEqual(not bool(certificate['error']), allowed, certificate)
                self.assertEqual(client.called, allowed)
                self.assertEqual(inspect.called, allowed)

    def test_certificate_inspection_to_other_panel_is_rejected(self):
        with patch('apps.baota.connection_views.inspect_certificate', return_value={}) as inspect:
            self.assertTrue(self.request(connection_views.CertificateView, 'post', {'id': self.other.pk})['error'])
        inspect.assert_not_called()

    def test_login_to_other_panel_is_rejected_before_client_creation(self):
        with patch('apps.baota.login_views.PanelClient') as client:
            self.assertTrue(self.request(login_views.LoginView, 'post', {'id': self.other.pk})['error'])
        client.assert_not_called()

    def test_zero_default_quota_rejects_new_panel_without_committing(self):
        with patch('apps.baota.views.encrypt_secret', return_value='cipher'):
            result = self.request(views.PanelView, 'post', {'name': 'new', 'group_id': self.group.pk,
                'url': 'https://example.invalid', 'username': 'panel', 'password': 'fixture'})
        self.assertTrue(result['error'])
        self.assertEqual(Panel.objects.count(), 2)

    def test_new_panel_belongs_to_current_account_under_quota(self):
        AccountResourcePolicy.objects.create(user=self.a, server_limit=2)
        with patch('apps.baota.views.encrypt_secret', return_value='cipher'):
            result = self.request(views.PanelView, 'post', {'name': 'new', 'group_id': self.group.pk,
                'url': 'https://example.invalid', 'username': 'panel', 'password': 'fixture', 'created_by_id': self.b.pk})
        self.assertFalse(result['error'], result)
        self.assertEqual(Panel.objects.get(pk=result['data']['id']).created_by_id, self.a.pk)

    def test_manage_grant_cannot_change_real_owners_group(self):
        ResourceGrant.objects.create(user=self.a, panel=self.other, level='manage')
        result = self.request(views.PanelView, 'post', {'id': self.other.pk, 'name': 'other',
            'group_id': self.group.pk, 'url': 'https://example.invalid', 'username': 'panel', 'password': ''})
        self.assertTrue(result['error'])
        self.other.refresh_from_db()
        self.assertEqual(self.other.group_id, self.other_group.pk)

    def test_panel_edit_rechecks_manage_after_quota_lock_wait(self):
        grant = ResourceGrant.objects.create(user=self.a, panel=self.other, level='manage')
        @contextmanager
        def gate(*args, **kwargs):
            grant.level = 'read'
            grant.save()
            yield
        with patch('apps.baota.views.access.quota_guard', gate):
            result = self.request(views.PanelView, 'post', {'id': self.other.pk, 'name': 'changed',
                'group_id': self.other.group_id, 'url': 'https://example.invalid', 'username': 'panel', 'password': ''})
        self.assertTrue(result['error'])
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, 'b')

    def test_panel_delete_rechecks_feature_after_quota_lock_wait(self):
        @contextmanager
        def gate(*args, **kwargs):
            self.role.page_perms['baota']['panel'].remove('del')
            self.role.save()
            yield
        with patch('apps.baota.views.access.quota_guard', gate):
            result = self.request(views.PanelView, 'delete', {'id': self.panel.pk})
        self.assertTrue(result['error'])
        self.assertTrue(Panel.objects.filter(pk=self.panel.pk).exists())

    def test_panel_add_rechecks_feature_after_quota_lock_wait(self):
        @contextmanager
        def gate(*args, **kwargs):
            self.role.page_perms['baota']['panel'].remove('add')
            self.role.save()
            yield
        with patch('apps.baota.views.access.quota_guard', gate), \
                patch('apps.baota.views.encrypt_secret', return_value='fixture'):
            result = self.request(views.PanelView, 'post', {'name': 'new', 'group_id': self.group.pk,
                'url': 'https://example.invalid', 'username': 'panel', 'password': 'fixture'})
        self.assertTrue(result['error'])
        self.assertEqual(Panel.objects.count(), 2)

    def test_managed_panel_does_not_reveal_unassigned_group(self):
        ResourceGrant.objects.create(user=self.a, panel=self.other, level='manage')
        other = next(p for p in self.request(views.PanelView)['data'] if p['id'] == self.other.pk)
        self.assertIsNone(other['group_id'])

    def test_group_edit_cannot_claim_unowned_group(self):
        result = self.request(views.GroupView, 'post', {'id': self.other_group.pk, 'name': 'stolen'})
        self.assertTrue(result['error'])

    def test_mixed_selected_targets_are_rejected_whole(self):
        with self.assertRaises(PermissionError):
            s.prepare(self.a, {'operation': 'site_basedir', 'panel_ids': [self.panel.pk, self.other.pk],
                'params': {'sites': [1], 'enabled': True}})
        self.assertFalse(PanelTask.objects.exists())

    def test_execute_and_view_do_not_include_upgrade(self):
        with self.assertRaises(PermissionError):
            s.prepare(self.a, {'operation': 'panel_upgrade', 'panel_ids': [self.panel.pk], 'params': {'version': '13.1.0'}})

    def test_manage_feature_does_not_reveal_other_accounts_task(self):
        task = self.fixture_task(self.panel, owner=self.b)
        with self.assertRaises(PermissionError):
            s.owned(self.a, task.pk, 'view')

    def test_task_read_rechecks_all_current_target_resources(self):
        task = self.fixture_task(self.panel, self.other)
        with self.assertRaises(PermissionError):
            s.owned(self.a, task.pk, 'view')
        self.assertEqual(self.request(task_views.TasksView)['data']['total'], 0)

    def test_result_options_do_not_leak_panel_or_group(self):
        result = self.request(task_views.TaskOptionsView)['data']
        self.assertEqual([p['id'] for p in result['panels']], [self.panel.pk])
        self.assertEqual([g['id'] for g in result['groups']], [self.group.pk])
        self.assertNotIn('panel_upgrade', [op['code'] for op in result['operations']])

    def test_worker_revoked_second_target_stops_first_preflight_request(self):
        self.fixture_task(self.panel, self.other, state='preflight_queued')
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        operation = Mock()
        operation.preflight.side_effect = lambda c, p: c.request('/system')
        with patch('apps.baota.worker.get_operation', return_value=operation):
            PanelWorker(client_factory=lambda p: client).run_once()
        client.request.assert_not_called()

    def test_worker_rechecks_permissions_between_read_requests(self):
        self.fixture_task(self.panel, state='preflight_queued')
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.request.side_effect = lambda *a, **k: self.a.roles.clear()
        def preflight(c, p):
            c.request('/system')
            c.request('/data')
        operation = Mock(preflight=preflight)
        with patch('apps.baota.worker.get_operation', return_value=operation):
            PanelWorker(client_factory=lambda p: client).run_once()
        self.assertEqual(client.request.call_count, 1)

    def test_worker_api_revision_change_between_read_and_mutation_stops_send(self):
        self.panel.api_config_revision = 1
        self.panel.save()
        task = self.fixture_task(self.panel)
        s.start(self.a, task.pk, {'acknowledgements': []})
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.request.side_effect = lambda *a, **k: Panel.objects.filter(pk=self.panel.pk).update(api_config_revision=2)
        def dispatch(c, *args):
            c.request('/read')
            c.request('/mutation', mutating=True)
            return {'state': 'success', 'message': 'fixture', 'evidence': {}}
        with patch('apps.baota.worker.get_operation', return_value=Mock(dispatch=dispatch)):
            PanelWorker(client_factory=lambda p: client).run_once()
        self.assertEqual(client.request.call_count, 1)
        target = task.targets.get()
        self.assertEqual(target.children[0]['state'], 'uncertain')
        self.assertEqual(target.state, 'uncertain')

    def test_worker_preflight_rechecks_api_revision_between_reads(self):
        self.fixture_task(self.panel, state='preflight_queued')
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.request.side_effect = lambda *a, **k: Panel.objects.filter(pk=self.panel.pk).update(api_config_revision=2)
        def preflight(c, params):
            c.request('/system')
            c.request('/data')
        with patch('apps.baota.worker.get_operation', return_value=Mock(preflight=preflight)):
            PanelWorker(client_factory=lambda p: client).run_once()
        self.assertEqual(client.request.call_count, 1)

    def test_worker_sibling_api_revision_change_stops_current_mutation(self):
        sibling = self.make_panel(self.a, self.group)
        task = self.fixture_task(self.panel, sibling)
        s.start(self.a, task.pk, {'acknowledgements': []})
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.request.side_effect = lambda *a, **k: Panel.objects.filter(pk=sibling.pk).update(api_config_revision=2)
        def dispatch(c, *args):
            c.request('/read')
            c.request('/mutation', mutating=True)
            return {'state': 'success', 'message': 'fixture', 'evidence': {}}
        with patch('apps.baota.worker.get_operation', return_value=Mock(dispatch=dispatch)):
            PanelWorker(client_factory=lambda p: client).run_once()
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(task.targets.get(panel=self.panel).state, 'uncertain')

    def test_worker_reloads_inactive_creator_instead_of_trusting_object(self):
        self.fixture_task(self.panel, state='preflight_queued')
        User.objects.filter(pk=self.a.pk).update(is_active=False)
        with patch('apps.baota.worker.PanelClient') as client:
            PanelWorker(client_factory=client).run_once()
        client.assert_not_called()

    def test_retry_requires_action_and_all_targets(self):
        task = self.fixture_task(self.panel, self.other)
        with self.assertRaises(PermissionError):
            s.retry(self.a, task.pk)

    def test_ssl_derived_task_requires_monitor_authorization_even_before_read(self):
        task = self.fixture_task(self.panel, operation='site_basedir', state='preflight_queued')
        task.audit = [{'action': 'ssl_monitor'}]
        task.save()
        with patch('apps.monitor.ssl.renewal.panel_runtime_authorized', return_value=False), \
                patch('apps.baota.worker.PanelClient') as client:
            PanelWorker(client_factory=client).run_once()
        client.assert_not_called()

    def test_ssl_monitor_authorization_does_not_replace_independent_panel_action(self):
        task = self.fixture_task(self.panel, operation='ssl_renew', state='preflight_queued')
        task.audit = [{'action': 'ssl_monitor'}]
        task.save()
        with patch('apps.monitor.ssl.renewal.panel_runtime_authorized', return_value=True), \
                patch('apps.baota.worker.PanelClient') as client:
            PanelWorker(client_factory=client).run_once()
        client.assert_not_called()

    def test_ssl_derived_preflight_also_requires_execute_feature(self):
        task = self.fixture_task(self.panel, operation='ssl_renew', state='preflight_queued')
        task.audit = [{'action': 'ssl_monitor'}]
        task.save()
        self.role.page_perms['exec']['panel_action'] = ['ssl_renew']
        self.role.page_perms['exec']['panel'] = ['view', 'create']
        self.role.save()
        with patch('apps.monitor.ssl.renewal.panel_runtime_authorized', return_value=True), \
                patch('apps.baota.worker.PanelClient') as client:
            PanelWorker(client_factory=client).run_once()
        client.assert_not_called()

    def test_ssl_source_task_cannot_lose_source_authorization_through_retry(self):
        task = self.fixture_task(self.panel)
        task.audit = [{'action': 'ssl_monitor'}]
        task.save()
        with self.assertRaisesRegex(ValueError, 'SSL'):
            s.retry(self.a, task.pk)
        self.assertEqual(PanelTask.objects.count(), 1)

    def test_ssl_source_task_cannot_become_unscoped_schedule_template(self):
        task = self.fixture_task(self.panel)
        task.audit = [{'action': 'ssl_monitor'}]
        task.save()
        with self.assertRaisesRegex(ValueError, 'SSL'):
            panel_tasks.template(self.a, task.pk)

    def test_action_rejection_explains_missing_permission(self):
        with self.assertRaisesRegex(PermissionError, 'exec.panel_action.panel_upgrade'):
            s.prepare(self.a, {'operation': 'panel_upgrade', 'panel_ids': [self.panel.pk], 'params': {'version': '13.1.0'}})

    def test_history_cannot_follow_forged_panel_task_id(self):
        task = self.schedule()
        other = self.fixture_task(self.other, owner=self.b)
        history = History.objects.create(task_id=task.pk, status=1, run_time='2026-11-01 12:00:00',
            output=panel_tasks.encode({'kind': 'baota', 'phase': 'completed', 'message': 'done', 'panel_task_id': other.pk}))
        with self.assertRaises(PermissionError):
            panel_tasks.history_detail(self.a, task, history)

    def test_schedule_list_hides_latest_of_inaccessible_or_unbound_panel_run(self):
        task = self.schedule()
        for private in (True, False):
            with self.subTest(private=private):
                run = self.fixture_task(self.other if private else self.panel, owner=self.b if private else self.a)
                history = History.objects.create(task_id=task.pk, status=2, run_time='2026-11-01 12:00:00',
                    output=panel_tasks.encode({'kind': 'baota', 'phase': 'completed', 'message': 'failed', 'panel_task_id': run.pk}))
                task.latest = history
                task.save(update_fields=['latest'])
                row = self.request(schedule_views.Schedule)['data']['tasks'][0]
                for field in ('latest_id', 'latest_status', 'latest_status_alias', 'latest_run_time'):
                    self.assertIsNone(row.get(field), row)

    def test_tick_cannot_cancel_unrelated_task_from_forged_history_id(self):
        task = self.schedule()
        panel_tasks.activate(self.a, task.pk, True, confirmed=True)
        task.refresh_from_db()
        other = self.fixture_task(self.other, owner=self.b)
        History.objects.create(task_id=task.pk, status=0, run_time='2026-11-01 12:00:00',
            output=panel_tasks.encode({'kind': 'baota', 'phase': 'preflight', 'message': 'preflight',
                'panel_task_id': other.pk, 'revision': panel_tasks.definition(task)['revision']}))
        panel_tasks.tick()
        other.refresh_from_db()
        self.assertFalse(other.cancel_requested)

    def test_stale_plan_object_cannot_preview_different_current_targets(self):
        task = self.schedule()
        stale = Task.objects.get(pk=task.pk)
        value = panel_tasks.definition(task)
        value['panel_ids'] = [self.other.pk]
        for field in ('params_by_panel', 'scope', 'revisions'):
            value[field] = {str(self.other.pk): next(iter(value[field].values()))}
        task.command = panel_tasks.encode(value)
        task.save()
        with self.assertRaises(PermissionError):
            panel_tasks.preview(self.a, stale)

    def schedule(self, creator=None):
        source = self.fixture_task(self.panel)
        template = panel_tasks.template(self.a, source.pk)
        task = panel_tasks.save(self.a, {'name': 'planned', 'type': 'panel', 'interpreter': 'baota',
            'panel_plan': {k: template[k] for k in ('template_id', 'template_token', 'acknowledgements')},
            'trigger': 'interval', 'trigger_args': 3600})
        if creator:
            task.created_by = creator
            task.save()
        return task

    def test_schedule_manage_feature_does_not_bypass_creator(self):
        task = self.schedule(creator=self.b)
        with self.assertRaises(PermissionError):
            panel_tasks.access(self.a, task)

    def test_schedule_actor_is_real_creator_not_stored_admin_id(self):
        task = self.schedule()
        value = panel_tasks.definition(task)
        value['authorized_by'] = self.admin.pk
        task.command, task.is_active = panel_tasks.encode(value), True
        task.save()
        self.a.roles.clear()
        before = PanelTask.objects.count()
        panel_tasks.dispatch(task.pk)
        self.assertEqual(PanelTask.objects.count(), before)
        task.refresh_from_db()
        self.assertFalse(task.is_active)

    def test_schedule_enable_permission_is_independent_of_edit(self):
        task = self.schedule()
        self.role.page_perms['schedule']['schedule'] = ['view', 'edit']
        self.role.save()
        with self.assertRaises(PermissionError):
            panel_tasks.activate(self.a, task.pk, True, confirmed=True)

    def test_schedule_activation_rechecks_scope(self):
        task = self.schedule()
        from apps.account.models import ResourceOwnership
        ResourceOwnership.objects.create(panel=self.panel, owner=self.b)
        with self.assertRaises(PermissionError):
            panel_tasks.activate(self.a, task.pk, True, confirmed=True)

    def test_panel_failure_notification_requires_current_run_and_action(self):
        from apps.schedule.utils import send_fail_notify
        for revoked in ('run', 'execute', 'action', 'manage'):
            with self.subTest(revoked=revoked):
                task = self.schedule()
                task.rst_notify = json.dumps({'mode': '2', 'value': 'https://example.invalid/notify'})
                task.save()
                original = json.loads(json.dumps(self.role.page_perms))
                if revoked == 'run':
                    self.role.page_perms['schedule']['schedule'].remove('run')
                elif revoked == 'execute':
                    self.role.page_perms['exec']['panel'].remove('execute')
                elif revoked == 'action':
                    self.role.page_perms['exec']['panel_action'].remove('site_basedir')
                else:
                    from apps.account.models import ResourceOwnership
                    ResourceOwnership.objects.create(panel=self.panel, owner=self.b)
                    ResourceGrant.objects.create(user=self.a, panel=self.panel, level='read')
                self.role.save()
                with patch('apps.schedule.utils.Notification.handle_request') as send:
                    send_fail_notify(task, 'fixture')
                self.role.page_perms = original
                self.role.save()
                if revoked == 'manage':
                    ResourceOwnership.objects.filter(panel=self.panel).delete()
                    ResourceGrant.objects.filter(user=self.a, panel=self.panel).delete()
                send.assert_not_called()


class ScriptScheduleResourceTests(TestCase):
    # Reuse account/request fixtures without duplicating the panel tests.
    request = PanelResourceTests.request
    make_panel = PanelResourceTests.make_panel

    def setUp(self):
        # These worker callbacks run inline on TestCase's transaction-owning
        # thread; their production connection cleanup belongs to worker threads.
        for target in ('apps.schedule.scheduler.connections.close_all',
                       'apps.schedule.executors.close_old_connections'):
            cleanup = patch(target)
            cleanup.start()
            self.addCleanup(cleanup.stop)
        PanelResourceTests.setUp(self)
        self.host = Host.objects.create(name='a', hostname='192.0.2.1', username='root', pkey='fixture', created_by=self.a)
        self.other_host = Host.objects.create(name='b', hostname='192.0.2.2', username='root', pkey='fixture', created_by=self.b)

    def script(self, owner=None, targets=None, active=True):
        return Task.objects.create(created_by=owner or self.a, name='script', type='test', interpreter='sh',
            command='echo fixture', targets=json.dumps(targets or [self.host.pk]), trigger='interval',
            trigger_args='3600', is_active=active)

    def script_data(self, **changes):
        value = dict(name='new', type='test', interpreter='sh', command='echo fixture', rst_notify={'mode': '0'},
            targets=[self.host.pk], trigger='interval', trigger_args='3600')
        value.update(changes)
        return value

    def test_script_list_only_shows_own_fully_authorized(self):
        mine = self.script()
        self.script(self.b)
        self.script(targets=[self.host.pk, self.other_host.pk])
        self.assertEqual([t['id'] for t in self.request(schedule_views.Schedule)['data']['tasks']], [mine.pk])

    def test_script_list_marks_read_shared_target_as_unmanageable(self):
        from apps.account import access
        access.set_policy(self.admin, self.a, 3)
        access.assign(self.admin, self.a, 'host', [self.other_host.pk], 'read')
        own = self.script()
        shared = self.script(targets=[self.other_host.pk])
        rows = self.request(schedule_views.Schedule)['data']['tasks']
        self.assertTrue(next(t for t in rows if t['id'] == own.pk)['can_manage'])
        self.assertFalse(next(t for t in rows if t['id'] == shared.pk)['can_manage'])

    def test_script_create_rejects_mixed_targets(self):
        result = self.request(schedule_views.Schedule, 'post', self.script_data(targets=[self.host.pk, self.other_host.pk]))
        self.assertTrue(result['error'])
        self.assertFalse(Task.objects.exists())

    def test_script_create_does_not_allow_normal_account_local_shell(self):
        result = self.request(schedule_views.Schedule, 'post', self.script_data(targets=['local']))
        self.assertTrue(result['error'])

    def test_script_edit_rejects_other_creator(self):
        task = self.script(self.b)
        result = self.request(schedule_views.Schedule, 'post', self.script_data(id=task.pk, name='stolen'))
        self.assertTrue(result['error'])
        task.refresh_from_db()
        self.assertEqual(task.name, 'script')

    def test_admin_script_edit_checks_new_targets_for_true_creator(self):
        task = self.script(active=False)
        result = self.request(schedule_views.Schedule, 'post',
            self.script_data(id=task.pk, targets=[self.other_host.pk]), user=self.admin)
        self.assertTrue(result['error'])
        task.refresh_from_db()
        self.assertEqual(json.loads(task.targets), [self.host.pk])

    def test_history_detail_checks_actual_historical_hosts(self):
        task = self.script()
        history = History.objects.create(task_id=task.pk, status=1, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.other_host.pk): [0, 1, 'b-private-output']}))
        result = self.request(schedule_views.HistoryView, data={'id': history.pk}, t_id=task.pk)
        self.assertTrue(result['error'])
        self.assertNotIn('b-private-output', json.dumps(result))

    def test_schedule_list_omits_latest_summary_with_unauthorized_historical_targets(self):
        task = self.script()
        history = History.objects.create(task_id=task.pk, status=2, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): [0, 1, 'own'], str(self.other_host.pk): [1, 1, 'private']}))
        task.latest = history
        task.save(update_fields=['latest'])
        row = self.request(schedule_views.Schedule)['data']['tasks'][0]
        self.assertIsNone(row.get('latest_id'))
        for field in ('latest_status', 'latest_status_alias', 'latest_run_time'):
            self.assertIsNone(row.get(field), row)

    def test_schedule_latest_requires_own_task_binding_even_for_admin(self):
        task, other = self.script(), self.script()
        history = History.objects.create(task_id=other.pk, status=1, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): [0, 1, 'own']}))
        task.latest = history
        task.save(update_fields=['latest'])
        for user in (self.a, self.admin):
            rows = self.request(schedule_views.Schedule, user=user)['data']['tasks']
            row = next(row for row in rows if row['id'] == task.pk)
            self.assertIsNone(row['latest_id'], row)
        history.task_id = task.pk
        history.save(update_fields=['task_id'])
        rows = self.request(schedule_views.Schedule)['data']['tasks']
        row = next(row for row in rows if row['id'] == task.pk)
        self.assertEqual(row['latest_id'], history.pk)
        self.assertEqual(row['latest_status'], 1)
        self.assertEqual(row['latest_run_time'], history.run_time)

    def test_history_list_omits_records_with_unauthorized_historical_hosts(self):
        task = self.script()
        own = History.objects.create(task_id=task.pk, status=1, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): [0, 1, 'own']}))
        History.objects.create(task_id=task.pk, status=1, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.other_host.pk): [0, 1, 'private']}))
        result = self.request(schedule_views.HistoryView, t_id=task.pk)
        self.assertEqual([row['id'] for row in result['data']], [own.pk])

    def test_add_feature_does_not_include_script_edit(self):
        task = self.script()
        self.role.page_perms['schedule']['schedule'] = ['add']
        self.role.save()
        self.assertTrue(self.request(schedule_views.Schedule, 'post', self.script_data(id=task.pk))['error'])

    def test_script_enable_is_independent_of_edit(self):
        task = self.script(active=False)
        self.role.page_perms['schedule']['schedule'] = ['edit']
        self.role.save()
        with patch('apps.schedule.views.get_redis_connection'):
            self.assertTrue(self.request(schedule_views.Schedule, 'patch', {'id': task.pk, 'is_active': True})['error'])

    def test_script_run_is_independent_of_edit_and_cannot_leak_history(self):
        task = self.script(self.b)
        self.assertTrue(self.request(schedule_views.HistoryView, t_id=task.pk)['error'])
        with patch('apps.schedule.views.dispatch_job') as dispatch:
            self.assertTrue(self.request(schedule_views.HistoryView, 'post', t_id=task.pk)['error'])
        dispatch.assert_not_called()

    def test_manual_script_revoke_keeps_executed_prefix_in_durable_history(self):
        second = Host.objects.create(name='a-2', hostname='192.0.2.3', username='root', pkey='fixture', created_by=self.a)
        task = self.script(targets=[self.host.pk, second.pk], active=False)
        sent = []
        def remote(host_id, interpreter, command, authorize):
            authorize()
            sent.append(host_id)
            self.a.roles.clear()
            return 0, 1, 'first completed'
        with patch('apps.schedule.views.dispatch_job', side_effect=remote):
            result = self.request(schedule_views.HistoryView, 'post', t_id=task.pk)
        self.assertEqual(sent, [self.host.pk])
        self.assertEqual(History.objects.filter(task_id=task.pk).count(), 1)
        history = History.objects.get(task_id=task.pk)
        self.assertEqual(history.status, 2)
        output = json.loads(history.output)
        self.assertEqual(output[str(self.host.pk)], [0, 1, 'first completed'])
        self.assertEqual(output[str(second.pk)][0], 1)
        self.assertEqual(result['data'], history.pk)

    def test_manual_script_history_terminal_guard_stops_first_request(self):
        from apps.schedule import resources
        task = self.script(active=False)
        sent = []
        def remote(host_id, interpreter, command, authorize):
            history = History.objects.get(task_id=task.pk, status=0)
            resources.fail_history(history.pk, 'stopped before send')
            authorize()
            sent.append(host_id)
            return 0, 0, 'executed'
        with patch('apps.schedule.views.dispatch_job', side_effect=remote):
            self.request(schedule_views.HistoryView, 'post', t_id=task.pk)
        self.assertEqual(sent, [])
        self.assertEqual(History.objects.filter(task_id=task.pk).count(), 1)
        history = History.objects.get(task_id=task.pk)
        self.assertEqual(history.status, 2)
        self.assertIn('stopped before send', history.output)

    def test_script_scheduler_checks_all_targets_before_publishing_first(self):
        task = self.script(targets=[self.host.pk, self.other_host.pk])
        with patch('apps.schedule.scheduler.get_redis_connection') as redis:
            Scheduler.dispatch(task.pk, task.interpreter, task.command, [self.host.pk])
        redis.assert_not_called()

    def test_script_worker_checks_all_targets_after_queued_revoke(self):
        task = self.script(targets=[self.host.pk, self.other_host.pk])
        history = History.objects.create(task_id=task.pk, status=0, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): None, str(self.other_host.pk): None}))
        with patch('apps.schedule.executors.dispatch_job', return_value=(0, 0, 'fixture')) as dispatch:
            executors.schedule_worker_handler(json.dumps([history.pk, self.host.pk, task.interpreter, task.command]))
        dispatch.assert_not_called()
        history.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertIn('授权', json.loads(history.output)[str(self.host.pk)][2])

    def test_script_worker_rejects_stale_command_in_queue(self):
        task = self.script()
        history = History.objects.create(task_id=task.pk, status=0, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): None}))
        with patch('apps.schedule.executors.dispatch_job', return_value=(0, 0, 'fixture')) as dispatch:
            executors.schedule_worker_handler(json.dumps([history.pk, self.host.pk, task.interpreter, 'old command']))
        dispatch.assert_not_called()

    def test_script_worker_interpreter_change_settles_history(self):
        task = self.script()
        history = History.objects.create(task_id=task.pk, status=0, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): None}))
        Task.objects.filter(pk=task.pk).update(interpreter='python')
        with patch('apps.schedule.executors.dispatch_job') as dispatch:
            executors.schedule_worker_handler(json.dumps([history.pk, self.host.pk, 'sh', task.command]))
        dispatch.assert_not_called()
        history.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertTrue(all(json.loads(history.output).values()))

    def test_running_worker_cannot_send_after_history_becomes_terminal(self):
        from apps.schedule import resources
        task = self.script()
        history = History.objects.create(task_id=task.pk, status=0, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): None}))
        sent = []
        def remote(host_id, interpreter, command, authorize):
            resources.fail_history(history.pk, 'partial publish failed')
            authorize()
            sent.append(host_id)
            return 0, 0, 'executed'
        with patch('apps.schedule.executors.dispatch_job', side_effect=remote):
            executors.schedule_worker_handler(json.dumps([history.pk, self.host.pk, 'sh', task.command]))
        self.assertEqual(sent, [])
        history.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertIn('partial publish failed', history.output)

    def test_running_worker_rechecks_history_target_before_each_send(self):
        task = self.script()
        history = History.objects.create(task_id=task.pk, status=0, run_time='2026-11-01 12:00:00',
            output=json.dumps({str(self.host.pk): None}))
        sent = []
        def remote(host_id, interpreter, command, authorize):
            History.objects.filter(pk=history.pk).update(output=json.dumps({str(self.other_host.pk): None}))
            authorize()
            sent.append(host_id)
            return 0, 0, 'executed'
        with patch('apps.schedule.executors.dispatch_job', side_effect=remote):
            executors.schedule_worker_handler(json.dumps([history.pk, self.host.pk, 'sh', task.command]))
        self.assertEqual(sent, [])

    def test_script_partial_publish_revoke_settles_history(self):
        second = Host.objects.create(name='a-2', hostname='192.0.2.3', username='root', pkey='fixture', created_by=self.a)
        task = self.script(targets=[self.host.pk, second.pk])
        with patch('apps.schedule.scheduler.get_redis_connection') as redis:
            redis.return_value.rpush.side_effect = lambda *args: self.a.roles.clear()
            try:
                Scheduler.dispatch(task.pk, task.interpreter, task.command, [])
            except PermissionError:
                pass
        self.assertEqual(redis.return_value.rpush.call_count, 1)
        history = History.objects.get(task_id=task.pk)
        self.assertEqual(history.status, 2)
        self.assertTrue(all(json.loads(history.output).values()))
        self.assertNotIn('echo fixture', history.output)

    def test_monitor_placeholder_requires_owned_authorized_detection(self):
        task = self.script(targets=['monitor'])
        task.trigger, task.trigger_args = 'monitor', '[999]'
        task.save()
        with patch('apps.schedule.scheduler.get_redis_connection') as redis:
            Scheduler.dispatch(task.pk, 'sh', task.command, [self.host.pk])
        redis.assert_not_called()

    def test_monitor_schedule_create_validates_detection(self):
        result = self.request(schedule_views.Schedule, 'post',
            self.script_data(targets=['monitor'], trigger='monitor', trigger_args='[999]'))
        self.assertTrue(result['error'])
        self.assertFalse(Task.objects.exists())

    def test_revoked_schedule_creator_cannot_send_failure_notification(self):
        from apps.schedule.utils import send_fail_notify
        task = self.script(targets=[self.host.pk, self.other_host.pk])
        task.rst_notify = json.dumps({'mode': '2', 'value': 'https://example.invalid/notify'})
        task.save()
        with patch('apps.schedule.utils.Notification.handle_request') as send:
            send_fail_notify(task, 'fixture')
        send.assert_not_called()

    def test_direct_failure_notify_reloads_creator_and_role(self):
        from apps.schedule.utils import _do_notify
        task = self.script()
        self.a.roles.clear()
        with patch('apps.schedule.utils.Notification.handle_request') as send:
            _do_notify(task, '2', 'https://example.invalid/notify', 'fixture')
        send.assert_not_called()

    def test_script_failure_notification_requires_current_run_and_manage(self):
        from apps.schedule.utils import send_fail_notify
        task = self.script()
        task.rst_notify = json.dumps({'mode': '2', 'value': 'https://example.invalid/notify'})
        task.save()
        for revoked in ('run', 'manage'):
            with self.subTest(revoked=revoked):
                original = json.loads(json.dumps(self.role.page_perms))
                if revoked == 'run':
                    self.role.page_perms['schedule']['schedule'].remove('run')
                    self.role.save()
                else:
                    from apps.account.models import ResourceOwnership
                    ResourceOwnership.objects.create(host=self.host, owner=self.b)
                    ResourceGrant.objects.create(user=self.a, host=self.host, level='read')
                with patch('apps.schedule.utils.Notification.handle_request') as send:
                    send_fail_notify(task, 'fixture')
                self.role.page_perms = original
                self.role.save()
                send.assert_not_called()
