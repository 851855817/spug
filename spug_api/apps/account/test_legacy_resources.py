"""Legacy entry points must carry the actual actor and all resource targets."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, RequestFactory
from apps.account.models import User, Role
from apps.host.models import Host
from apps.exec.models import ExecTemplate
from apps.apis import exec as api_exec
from apps.pipeline import utils as pipeline
from apps.pipeline.models import Pipeline, PipeHistory
from apps.pipeline.views import PipeView, HistoryView


class LegacyResourceTests(TestCase):
    def setUp(self):
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b')
        self.a.roles.add(Role.objects.create(name='executor', page_perms={'exec': {'task': ['do']}}))
        self.host = Host.objects.create(name='a', hostname='192.0.2.1', username='root', pkey='fixture', created_by=self.a)
        self.other = Host.objects.create(name='b', hostname='192.0.2.2', username='root', pkey='fixture', created_by=self.b)
        self.template = ExecTemplate.objects.create(name='check', type='fixture', body='true',
                                                   created_by=self.a, host_ids=json.dumps([self.host.pk]))
        self.factory = RequestFactory()

    def trigger(self, ids=None):
        request = self.factory.post('/', json.dumps({'host_ids': ids} if ids else {}), content_type='application/json')
        return api_exec.trigger.__wrapped__(request, self.template.pk)

    def test_api_owner_cannot_execute_foreign_template_target(self):
        self.template.host_ids = json.dumps([self.other.pk])
        self.template.save()
        with patch.object(api_exec, 'get_redis_connection') as redis:
            self.assertEqual(self.trigger().status_code, 403)
            redis.assert_not_called()

    def test_api_job_carries_template_actor_identity(self):
        redis = Mock()
        with patch.object(api_exec, 'get_redis_connection', return_value=redis):
            self.assertEqual(self.trigger().status_code, 202)
        job = json.loads(redis.rpush.call_args.args[1])
        self.assertEqual(job.get('template_id'), self.template.pk)

    def test_api_rechecks_owner_before_returning_result(self):
        redis = Mock()
        redis.get.return_value = None
        meta = {'template_id': self.template.pk, 'hosts': {str(self.host.pk): 'fixture'}}
        redis.hgetall.return_value = {b'meta': json.dumps(meta).encode()}
        self.a.roles.clear()
        with patch.object(api_exec, 'get_redis_connection', return_value=redis):
            response = api_exec.result.__wrapped__(self.factory.get('/'), 'fixture')
        self.assertEqual(response.status_code, 403)
        redis.get.assert_not_called()

    def test_api_result_does_not_return_output_when_revoked_during_redis_read(self):
        redis = Mock()
        meta = {'template_id': self.template.pk, 'hosts': {str(self.host.pk): 'fixture'}}
        redis.hgetall.return_value = {b'meta': json.dumps(meta).encode()}
        def revoke(_):
            self.a.roles.clear()
            return b'secret-output'
        redis.get.side_effect = revoke
        with patch.object(api_exec, 'get_redis_connection', return_value=redis):
            response = api_exec.result.__wrapped__(self.factory.get('/'), 'fixture')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('secret-output', response.content.decode())

    def test_pipeline_snapshot_covers_source_build_and_destinations(self):
        snapshot = SimpleNamespace(nodes=json.dumps([
            {'id': '1', 'module': 'data_transfer', 'source': {'target': self.host.pk},
             'destination': {'targets': [self.other.pk]}, '_targets': [{'id': self.other.pk}]},
            {'id': '2', 'module': 'build', 'target': 77},
            {'id': '3', 'module': 'ssh_exec', 'targets': [88], '_targets': [{'id': 88}]},
        ]))
        helper = getattr(pipeline, 'resource_host_ids', None)
        self.assertTrue(callable(helper), 'resource target resolver missing')
        self.assertEqual(helper(snapshot), {self.host.pk, self.other.pk, 77, 88})

    def test_pipeline_rejects_invalid_target_instead_of_dropping_it(self):
        helper = getattr(pipeline, 'resource_host_ids', None)
        self.assertTrue(callable(helper), 'resource target resolver missing')
        with self.assertRaises(ValueError):
            helper(SimpleNamespace(nodes='[{"module":"build","target":true}]'))

    def test_pipeline_historical_display_targets_are_included_in_access_boundary(self):
        helper = getattr(pipeline, 'resource_host_ids')
        snapshot = SimpleNamespace(nodes=json.dumps([{'module': 'ssh_exec', 'targets': [self.host.pk],
            '_targets': [{'id': self.other.pk, 'name': 'legacy hidden'}]}]))
        self.assertEqual(helper(snapshot), {self.host.pk, self.other.pk})

    def test_pipeline_lists_only_owned_fully_authorized_definitions(self):
        self.a.roles.add(Role.objects.create(name='pipeline', page_perms={'pipeline': {'pipeline': ['view']}}))
        own = Pipeline.objects.create(name='own', created_by=self.a,
            nodes=json.dumps([{'id': '1', 'module': 'ssh_exec', 'targets': [self.host.pk]}]))
        Pipeline.objects.create(name='foreign', created_by=self.b, nodes=own.nodes)
        Pipeline.objects.create(name='mixed', created_by=self.a,
            nodes=json.dumps([{'id': '1', 'module': 'ssh_exec', 'targets': [self.host.pk, self.other.pk]}]))
        request = self.factory.get('/')
        request.user = self.a
        response = json.loads(PipeView.as_view()(request).content)
        self.assertEqual([x['id'] for x in response['data']], [own.pk])

    def test_pipeline_history_does_not_disclose_another_accounts_outputs(self):
        self.a.roles.add(Role.objects.create(name='pipeline', page_perms={'pipeline': {'pipeline': ['view']}}))
        pipe = Pipeline.objects.create(name='foreign', created_by=self.b, nodes='[]')
        record = PipeHistory.objects.create(pipeline=pipe, ordinal=1, nodes='[]', created_by=self.b)
        request = self.factory.get('/')
        request.user = self.a
        with patch('apps.pipeline.views.get_redis_connection') as redis:
            response = HistoryView.as_view()(request, h_id=record.pk)
        self.assertTrue(json.loads(response.content)['error'])
        redis.assert_not_called()

    def test_pipeline_worker_rechecks_actual_history_owner_before_ssh(self):
        from libs import AttrDict
        self.a.roles.add(Role.objects.create(name='pipeline', page_perms={'pipeline': {'pipeline': ['do']}}))
        nodes = [{'id': '1', 'module': 'ssh_exec', 'targets': [self.host.pk], 'command': 'true'}]
        pipe = Pipeline.objects.create(name='own', created_by=self.a, nodes=json.dumps(nodes))
        record = PipeHistory.objects.create(pipeline=pipe, ordinal=1, nodes=json.dumps(nodes), created_by=self.a)
        executor = pipeline.NodeExecutor(Mock(), 'fixture', nodes, history_id=record.pk)
        self.addCleanup(executor.helper.clear)
        self.a.roles.clear()
        with patch.object(self.host, 'get_ssh') as connect:
            self.assertFalse(executor._ssh_exec(self.host, AttrDict(nodes[0])))
        connect.assert_not_called()

    def test_unattributed_legacy_notifications_are_admin_only(self):
        from apps.notify.models import Notify
        from apps.notify.views import NotifyView
        Notify.objects.create(title='other account connection failed', source='monitor', type='1')
        request = self.factory.get('/')
        request.user = self.a
        response = json.loads(NotifyView.as_view()(request).content)
        self.assertEqual(response['data'], [])

    def test_notice_write_requires_admin_but_mark_read_remains_personal(self):
        from apps.home.notice import NoticeView
        from apps.home.models import Notice
        request = self.factory.post('/', json.dumps({'title': 'injected', 'content': 'payload'}), content_type='application/json')
        request.user = self.a
        response = json.loads(NoticeView.as_view()(request).content)
        self.assertTrue(response['error'])
        self.assertFalse(Notice.objects.exists())

    def test_legacy_monitor_scope_excludes_foreign_and_mixed_detections(self):
        from apps.monitor.models import Detection
        from apps.monitor.views import DetectionView
        self.a.roles.add(Role.objects.create(name='monitor', page_perms={'monitor': {'monitor': ['view']}}))
        def make(owner, *hosts):
            return Detection.objects.create(created_by=owner, name='fixture', type='3',
                targets=json.dumps([h.pk for h in hosts]), extra='nginx', notify_grp='[]', notify_mode='[]')
        own = make(self.a, self.host)
        make(self.b, self.other)
        make(self.a, self.host, self.other)
        request = self.factory.get('/')
        request.user = self.a
        data = json.loads(DetectionView.as_view()(request).content)['data']
        self.assertEqual([row['id'] for row in data['detections']], [own.pk])

    def test_legacy_monitor_worker_checks_owner_and_all_targets_before_dispatch(self):
        from apps.monitor.models import Detection
        from apps.monitor import executors
        self.a.roles.add(Role.objects.create(name='monitor', page_perms={'monitor': {'monitor': ['edit']}}))
        task = Detection.objects.create(created_by=self.a, name='fixture', type='3',
            targets=json.dumps([self.host.pk, self.other.pk]), extra='nginx', notify_grp='[]', notify_mode='[]')
        with patch.object(executors, 'host_executor', return_value=(True, 'fixture')) as execute, \
                patch.object(executors, 'get_redis_connection') as redis:
            redis.return_value.hmget.return_value = (None, None)
            executors.monitor_worker_handler(json.dumps([task.pk, '3', self.host.pk, 'nginx', 1, 5]))
        execute.assert_not_called()
        redis.assert_not_called()

    def test_add_only_owner_can_run_the_monitor_they_created_enabled(self):
        from apps.monitor.models import Detection
        from apps.monitor import executors
        self.a.roles.add(Role.objects.create(name='monitor-add', page_perms={'monitor': {'monitor': ['add']}}))
        task = Detection.objects.create(created_by=self.a, name='site', type='1',
            targets=json.dumps(['https://example.com']), extra='', notify_grp='[]', notify_mode='[]')
        with patch.object(executors, 'site_check', return_value=(True, 'ok')) as probe, \
                patch.object(executors, 'get_redis_connection') as redis:
            redis.return_value.hmget.return_value = (None, None)
            executors.monitor_worker_handler(json.dumps([task.pk, '1', 'https://example.com', '', 1, 5]))
        probe.assert_called_once()

    def test_pipeline_api_rechecks_owner_before_creating_job(self):
        from apps.apis import pipeline as api
        pipe = Pipeline.objects.create(name='own', created_by=self.a,
            nodes=json.dumps([{'id': '1', 'module': 'ssh_exec', 'targets': [self.host.pk], 'command': 'true'}]))
        request = self.factory.post('/', '{}', content_type='application/json')
        with patch.object(api, 'get_redis_connection') as redis, patch.object(api, 'Thread'):
            response = api.trigger.__wrapped__(request, pipe.pk)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PipeHistory.objects.exists())
        redis.assert_not_called()

    def test_pipeline_worker_rejects_executable_nodes_that_differ_from_saved_snapshot(self):
        self.a.roles.add(Role.objects.create(name='pipeline', page_perms={'pipeline': {'pipeline': ['do']}}))
        saved = [{'id': '1', 'module': 'ssh_exec', 'targets': [self.host.pk], 'command': 'true'}]
        pipe = Pipeline.objects.create(name='own', created_by=self.a, nodes=json.dumps(saved))
        record = PipeHistory.objects.create(pipeline=pipe, ordinal=1, nodes=pipe.nodes, created_by=self.a)
        executable = [{'id': '1', 'module': 'ssh_exec', 'targets': [self.other.pk], 'command': 'true'}]
        executor = pipeline.NodeExecutor(Mock(), 'fixture', executable, history_id=record.pk)
        with self.assertRaises(PermissionError):
            executor._authorize()

    def test_pipeline_ticket_reloads_config_even_when_given_old_object(self):
        from apps.pipeline.views import require_ticket
        self.a.roles.add(Role.objects.create(name='pipeline', page_perms={'pipeline': {'pipeline': ['do']}}))
        pipe = Pipeline.objects.create(name='own', created_by=self.a,
            nodes=json.dumps([{'id': '1', 'module': 'ssh_exec', 'targets': [self.host.pk]}]))
        from apps.pipeline.resources import definition_digest
        meta = json.dumps({'owner_id': self.a.pk, 'pipeline_id': pipe.pk, 'digest': definition_digest(pipe)})
        Pipeline.objects.filter(pk=pipe.pk).update(nodes='[]')
        redis = Mock()
        redis.get.return_value = meta
        with patch('apps.pipeline.views.get_redis_connection', return_value=redis):
            with self.assertRaises(ValueError):
                require_ticket(self.a, 'a' * 32, pipe)

    def test_legacy_config_token_without_actor_is_not_an_authorization_bypass(self):
        from apps.apis.config import get_configs
        from apps.app.models import App
        app = App.objects.create(name='fixture', key='fixture', created_by=self.a)
        redis = Mock()
        redis.get.return_value = f'{app.pk},1'.encode()
        with patch('apps.apis.config.get_redis_connection', return_value=redis), \
                patch('apps.apis.config.compose_configs') as configs:
            response = get_configs(self.factory.get('/', {'apiToken': 'fixture'}))
        self.assertEqual(response.status_code, 403)
        configs.assert_not_called()

    def test_pipeline_output_key_does_not_allow_directory_traversal(self):
        from apps.pipeline.helper import Helper
        helper = Helper(Mock(), 'fixture')
        with self.assertRaises(ValueError):
            helper.get_file('../foreign')

    def test_pipeline_node_ids_must_be_distinct_path_components(self):
        from apps.pipeline.resources import authorize
        for identifier in ('..', '../foreign', 'a/b', '', True):
            record = SimpleNamespace(created_by_id=self.a.pk, nodes=json.dumps([
                {'id': identifier, 'module': 'parameter'}]))
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                authorize(self.a, record, manage=True)

    def test_dynamic_pipeline_ticket_can_start_only_once(self):
        from apps.pipeline.views import DoView
        from apps.pipeline.resources import definition_digest
        from contextlib import nullcontext
        self.a.roles.add(Role.objects.create(name='pipeline-run', page_perms={'pipeline': {'pipeline': ['do']}}))
        pipe = Pipeline.objects.create(name='own', created_by=self.a, nodes=json.dumps([
            {'id': 'one', 'module': 'parameter', 'dynamic_params': []}]))
        token = 'a' * 32
        store = {'pipeline-ticket:' + token: json.dumps({
            'owner_id': self.a.pk, 'pipeline_id': pipe.pk, 'digest': definition_digest(pipe)})}
        redis = Mock()
        redis.get.side_effect = store.get
        redis.delete.side_effect = lambda key: store.pop(key, None)
        redis.lock.side_effect = lambda *args, **kwargs: nullcontext()
        def start():
            request = self.factory.patch('/', json.dumps({'id': pipe.pk, 'token': token, 'params': {}}),
                                         content_type='application/json')
            request.user = self.a
            return json.loads(DoView.as_view()(request).content)
        with patch('apps.pipeline.views.get_redis_connection', return_value=redis), \
                patch('apps.pipeline.views.NodeExecutor'), patch('apps.pipeline.views.Thread'):
            self.assertFalse(start()['error'])
            self.assertTrue(start()['error'])
        self.assertEqual(PipeHistory.objects.filter(pipeline=pipe).count(), 1)

    def test_pipeline_cross_environment_files_are_scoped_to_execution(self):
        from apps.pipeline.helper import Helper
        from django.test import override_settings
        import tempfile
        with tempfile.TemporaryDirectory() as directory, override_settings(DEPLOY_DIR=directory):
            own = Helper(Mock(), 'a' * 32)
            other = Helper(Mock(), 'b' * 32)
            other.set_cross_env('one', {'SPUG_GEV_PRIVATE': 'private'})
            self.assertEqual(own.get_cross_env('one'), {})
            for key in ('../outside', '..'):
                with self.assertRaises(ValueError):
                    own.get_cross_env(key)

    def test_notification_receipts_are_personal_and_rechecked_on_read(self):
        from apps.notify.models import Notify, NotifyAudience
        from apps.notify.views import NotifyView
        from apps.account.models import ResourceGrant
        self.b.roles.add(*self.a.roles.all())
        with patch('apps.notify.models.Channel.send_notify'):
            Notify.make_monitor_notify('own', 'fixture', user=self.a, permission='exec.task.do',
                host_ids=[self.host.pk])
            Notify.make_monitor_notify('other', 'secret', user=self.b, permission='exec.task.do',
                host_ids=[self.other.pk])
        request = self.factory.get('/')
        request.user = self.a
        data = json.loads(NotifyView.as_view()(request).content)['data']
        self.assertEqual([row['title'] for row in data], ['own'])
        self.a.roles.clear()
        self.assertEqual(json.loads(NotifyView.as_view()(request).content)['data'], [])
        self.assertEqual(NotifyAudience.objects.count(), 2)

    def test_monitor_alarm_keeps_owner_and_all_target_snapshot(self):
        from apps.monitor.models import Detection
        from apps.monitor.utils import _record_alarm
        from apps.alarm.models import AlarmResource
        det = Detection.objects.create(created_by=self.a, name='fixture', type='3',
            targets=json.dumps([self.host.pk]), extra='nginx', notify_grp='[]', notify_mode='[]')
        _record_alarm(det, 'fixture', '1 min', '1')
        record = AlarmResource.objects.get()
        self.assertEqual(record.owner_id, self.a.pk)
        self.assertEqual(record.host_ids, [self.host.pk])

    # Run the worker inline without closing TestCase's owning DB transaction.
    @patch('apps.monitor.utils.close_old_connections')
    def test_monitor_notification_rechecks_owner_before_alarm_or_external_send(self, cleanup):
        from apps.monitor.models import Detection
        from apps.monitor.utils import handle_notify
        from apps.account.access import AccessDenied
        det = Detection.objects.create(created_by=self.a, name='fixture', type='3',
            targets=json.dumps([self.host.pk]), extra='nginx', notify_grp='[]', notify_mode='[]')
        with patch('apps.monitor.utils._record_alarm') as alarm, patch('apps.monitor.utils.Notification') as notify:
            with self.assertRaises(AccessDenied):
                handle_notify(det.pk, 'fixture', False, 'fixture', 1)
        alarm.assert_not_called()
        notify.assert_not_called()

    def test_alarm_contacts_do_not_disclose_another_users_destinations(self):
        from apps.alarm.models import Contact
        from apps.alarm.views import ContactView
        self.a.roles.add(Role.objects.create(name='alarm', page_perms={'alarm': {'contact': ['view']}}))
        own = Contact.objects.create(name='own', email='own@example.invalid', created_by=self.a)
        Contact.objects.create(name='other', email='secret@example.invalid', created_by=self.b)
        request = self.factory.get('/')
        request.user = self.a
        data = json.loads(ContactView.as_view()(request).content)['data']
        self.assertEqual([row['id'] for row in data], [own.pk])
