"""Scheduled mutations use isolated durable tasks; no real panel or SSH calls."""
import importlib
import json
import threading
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.baota import models
from apps.baota import test_tasks as fixtures
from apps.schedule.models import Task, History


class ScheduledPanelTests(TransactionTestCase):
    setUp = fixtures.PanelTaskTests.setUp
    make_user = fixtures.PanelTaskTests.make_user
    authenticate = fixtures.PanelTaskTests.authenticate
    request = fixtures.PanelTaskTests.request
    group = fixtures.PanelTaskTests.group
    panel = fixtures.PanelTaskTests.panel
    configured = fixtures.PanelTaskTests.configured
    services = fixtures.PanelTaskTests.services
    prepare = fixtures.PanelTaskTests.prepare
    prepared = fixtures.PanelTaskTests.prepared

    def module(self):
        self.services()
        module = importlib.import_module('apps.schedule.panel_tasks')
        patch('apps.schedule.panel_tasks.get_operation', return_value=self.op).start()
        return module

    def schedule(self, service, source=None, **changes):
        source = source or self.prepared()
        template = service.template(self.admin, source.pk)
        data = dict(name='证书定时续签', type='面板任务', interpreter='baota',
                    panel_plan={key: template[key] for key in ('template_id', 'template_token', 'acknowledgements')},
                    trigger='interval', trigger_args=3600, rst_notify={'mode': '0'}, desc='')
        data.update(changes)
        return service.save(self.admin, data)

    def active(self, service, **changes):
        task = self.schedule(service, **changes)
        service.activate(self.admin, task.pk, True, confirmed=True)
        task.refresh_from_db()
        return task

    def test_panel_definition_never_reaches_shell_executor(self):
        from .executors import dispatch_job
        with patch('apps.schedule.executors.local_executor') as shell:
            code, _, _ = dispatch_job('local', 'baota', '{"operation":"fixture"}')
        self.assertNotEqual(code, 0)
        shell.assert_not_called()

    def test_saves_fixed_scope_and_defaults_to_inactive(self):
        service = self.module()
        task = self.schedule(service)
        self.assertFalse(task.is_active)
        definition = json.loads(task.command)
        params = next(iter(definition['params_by_panel'].values()))
        self.assertEqual(params, {'sites': [1, 2]})
        self.assertNotIn('all_sites', params)
        self.assertEqual(definition['authorized_by'], None)
        self.assertEqual(json.loads(task.targets), [])  # Never share the Host ID namespace.

    def test_only_schedule_permission_cannot_save_panel_definition(self):
        service = self.module()
        source = self.prepared()
        user = self.make_user('scheduler', perms={'schedule': ['add', 'edit', 'view']})
        with self.assertRaises(PermissionError):
            service.template(user, source.pk)

    def test_activation_requires_confirmation(self):
        service = self.module()
        task = self.schedule(service)
        with self.assertRaises(ValueError):
            service.activate(self.admin, task.pk, True)
        task.refresh_from_db()
        self.assertFalse(task.is_active)

    def test_forged_template_token_is_rejected(self):
        service = self.module()
        source = self.prepared()
        template = service.template(self.admin, source.pk)
        with self.assertRaises(ValueError):
            self.schedule(service, source, panel_plan={'template_id': source.pk,
                          'template_token': '0' * 64, 'acknowledgements': []})
        self.assertFalse(Task.objects.exists())

    def test_dispatch_creates_new_preflight_without_mutation(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.assertEqual(history.status, 0)
        self.assertEqual(models.PanelTask.objects.count(), 2)
        self.assertFalse(self.op.sent)
        self.assertEqual(json.loads(history.output)['phase'], 'preflight')

    def test_full_preflight_then_auto_execution_and_history(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        self.assertFalse(self.op.sent)
        while self.worker.run_once():
            service.tick()
        service.tick()
        history.refresh_from_db()
        self.assertEqual(history.status, 1)
        self.assertEqual(len(self.op.sent), 2)
        detail = service.history_detail(self.admin, task, history)
        self.assertEqual(detail['kind'], 'baota')
        self.assertEqual(detail['success'], 1)

    def test_duplicate_or_overlap_trigger_does_not_create_second_preflight(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        service.dispatch(task.pk)
        self.assertEqual(models.PanelTask.objects.count(), 2)
        task.refresh_from_db()
        self.assertEqual(task.latest_id, history.pk)

    def test_disable_after_preflight_prevents_start(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.activate(self.admin, task.pk, False)
        service.tick()
        self.assertFalse(self.worker.run_once())
        history.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertFalse(self.op.sent)

    def test_revoked_account_prevents_next_mutation(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
        self.worker.run_once()
        service.tick()
        self.assertFalse(self.op.sent)
        task.refresh_from_db()
        self.assertFalse(task.is_active)

    def test_scope_drift_blocks_every_target_before_start(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.op.count = 3
        self.worker.run_once()
        service.tick()
        self.assertFalse(self.op.sent)
        task.refresh_from_db()
        history.refresh_from_db()
        self.assertFalse(task.is_active)
        self.assertEqual(history.status, 2)
        self.assertIn('范围', json.loads(history.output)['message'])

    def test_uncertainty_stops_plan_without_resending(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        self.op.dispatch_result = {'state': 'uncertain', 'message': '结果待核实', 'evidence': {}}
        self.worker.run_once()
        service.tick()
        service.dispatch(task.pk)
        history.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertFalse(task.is_active)
        self.assertEqual(len(self.op.sent), 1)
        self.assertEqual(models.PanelTask.objects.count(), 2)

    def test_unknown_interpreter_never_executes(self):
        from .executors import dispatch_job
        with patch('apps.schedule.executors.local_executor') as shell:
            code, _, _ = dispatch_job('local', 'invalid', 'touch /tmp/never')
        self.assertNotEqual(code, 0)
        shell.assert_not_called()

    @override_settings(ROOT_URLCONF='apps.schedule.test_urls')
    def test_http_save_activate_and_no_internal_definition_leak(self):
        service = self.module()
        source = self.prepared()
        plan = service.template(self.admin, source.pk)
        body = dict(name='HTTP plan', type='面板', interpreter='baota',
                    panel_plan={k: plan[k] for k in ('template_id', 'template_token', 'acknowledgements')},
                    trigger='interval', trigger_args=300, rst_notify={'mode': '0'})
        self.request('post', '/schedule/', body)
        task = Task.objects.get()
        records = self.request('get', '/schedule/')['tasks']
        self.assertEqual(records[0]['command'], '')
        self.assertNotIn('authorized_by', json.dumps(records))
        response = self.client.patch('/schedule/', json.dumps({'id': task.pk, 'is_active': True, 'confirm_panel': True}), content_type='application/json')
        self.assertFalse(response.json()['error'], response.content)
        task.refresh_from_db()
        self.assertTrue(task.is_active)

    @override_settings(ROOT_URLCONF='apps.schedule.test_urls')
    def test_http_cannot_reinterpret_panel_plan_as_shell(self):
        service = self.module()
        task = self.schedule(service)
        body = dict(id=task.pk, name='forged', type='script', interpreter='sh', command='echo forbidden',
                    targets=['local'], trigger='interval', trigger_args='300', rst_notify={'mode': '0'})
        self.request('post', '/schedule/', body, success=False)
        task.refresh_from_db()
        self.assertEqual(task.interpreter, 'baota')

    @override_settings(ROOT_URLCONF='apps.schedule.test_urls')
    def test_http_cannot_read_another_tasks_history(self):
        service = self.module()
        task = self.schedule(service)
        foreign = History.objects.create(task_id=task.pk + 100, status=1, run_time='2026-01-01 00:00:00', output='{}')
        self.request('get', f'/schedule/{task.pk}/', {'id': foreign.pk}, success=False)

    def test_one_failed_panel_prevents_all_mutations(self):
        service = self.module()
        first = self.configured()
        second = self.configured(name='second', group_id=first['group_id'])
        source = self.prepare(panel=first, panel_ids=[first['id'], second['id']])
        self.worker.run_once()
        self.worker.run_once()
        task = self.active(service, source=source)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        with patch.object(self.op, 'preflight', side_effect=ValueError('fixture failure')):
            self.worker.run_once()
        service.tick()
        self.assertFalse(self.op.sent)
        history.refresh_from_db()
        self.assertEqual(history.status, 2)

    def test_role_revocation_bypasses_stale_permission_cache(self):
        service = self.module()
        user = self.make_user('operator', perms={'panel': ['view', 'create', 'execute']})
        role = user.roles.get()
        role.page_perms = {'exec': {'panel': ['view', 'create', 'execute']},
                           'schedule': {'schedule': ['edit', 'view']}}
        role.save()
        self.assertTrue(service.permitted(user, 'exec.panel.execute'))
        self.assertTrue(user.has_perms(['exec.panel.execute']))
        task = self.active(service)
        value = service.definition(task)
        value['authorized_by'] = user.pk
        task.command = service.encode(value)
        task.save()
        role.page_perms = {'exec': {'panel': ['view', 'create']}, 'schedule': {'schedule': ['edit', 'view']}}
        role.save()
        self.assertTrue(user.has_perms(['exec.panel.execute']))  # old login cache
        history = service.dispatch(task.pk)
        self.assertEqual(history.status, 2)
        self.assertEqual(models.PanelTask.objects.count(), 1)

    def test_disabling_between_children_stops_remaining_and_releases_safe_lock(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        self.worker.run_once()
        self.assertEqual(len(self.op.sent), 1)
        service.activate(self.admin, task.pk, False)
        service.tick()
        self.assertFalse(models.PanelLock.objects.exists())
        self.assertFalse(self.worker.run_once())
        history.refresh_from_db()
        self.assertEqual(history.status, 2)

    def test_overlap_is_recorded_without_overwriting_inflight_latest(self):
        service = self.module()
        task = self.active(service)
        first = service.dispatch(task.pk)
        service.dispatch(task.pk)
        self.assertEqual(History.objects.filter(task_id=task.pk, status=3).count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.latest_id, first.pk)

    def test_cooldown_includes_last_mutation_not_only_preflight_start(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        while self.worker.run_once():
            service.tick()
        service.tick()
        history.refresh_from_db()
        output = json.loads(history.output)
        output['at'] = (timezone.now() - timedelta(minutes=10)).isoformat()
        history.output = service.encode(output)
        history.save()
        result = service.dispatch(task.pk)
        self.assertEqual(result.status, 3)
        self.assertEqual(models.PanelTask.objects.count(), 2)

    def test_schedule_preflight_cannot_be_started_through_manual_endpoint(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.op.count = 3
        self.worker.run_once()
        linked = models.PanelTask.objects.get(pk=json.loads(history.output)['panel_task_id'])
        with self.assertRaises(ValueError):
            self.service.start(self.admin, linked.pk, {'acknowledgements': []})
        self.assertFalse(self.worker.run_once())
        self.assertFalse(self.op.sent)

    def test_original_monitor_trigger_preserves_resolved_host(self):
        from .scheduler import Scheduler
        task = Task.objects.create(name='monitor shell', type='shell', interpreter='sh', command='echo safe',
            targets='["monitor"]', trigger='monitor', trigger_args='[1]', is_active=True, created_by=self.admin)
        with patch('apps.schedule.scheduler.get_redis_connection') as redis:
            Scheduler.dispatch(task.pk, 'sh', 'echo safe', [81])
        payload = json.loads(redis.return_value.rpush.call_args[0][1])
        self.assertEqual(payload[1], 81)

    def test_corrupt_definition_stops_only_its_run(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        Task.objects.filter(pk=task.pk).update(command='{}')
        service.tick()
        history.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertFalse(task.is_active)

    def test_ssl_skipped_site_keeps_explicit_domain_selection(self):
        service = self.module()
        source = self.prepared()
        source.operation = 'ssl_apply'
        source.save()
        target = source.targets.get()
        target.params = {'sites': [1, 2], 'domains': {'1': ['one.example.com'], '2': ['two.example.com']}}
        target.children[1]['snapshot']['state'] = 'skipped'
        target.save()
        plan = service.frozen(source)
        self.assertEqual(plan['params_by_panel'][str(target.panel_id)]['domains'], target.params['domains'])

    def test_ssl_unknown_skipped_domain_scope_cannot_be_scheduled(self):
        service = self.module()
        source = self.prepared()
        source.operation = 'ssl_apply'
        source.save()
        with self.assertRaises(ValueError):
            service.frozen(source)

    def test_failure_notifier_is_claimed_once_without_retrying_transport(self):
        service = self.module()
        task = self.active(service)
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
        history = service.dispatch(task.pk)
        with patch('apps.schedule.utils.send_fail_notify') as notify:
            service.notify_pending()
            service.notify_pending()
        notify.assert_called_once()
        history.refresh_from_db()
        self.assertEqual(json.loads(history.output)['notification'], 'attempted')

    def test_daily_pruning_keeps_pinned_inflight_history_after_many_skips(self):
        from .builtin import auto_run_by_day
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        for _ in range(55):
            service.dispatch(task.pk)
        auto_run_by_day()
        self.assertTrue(History.objects.filter(pk=history.pk).exists())

    def test_restart_after_preflight_resumes_once(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        self.worker_module.recover_interrupted()
        service.tick()
        while self.worker.run_once():
            service.tick()
        history.refresh_from_db()
        self.assertEqual(history.status, 1)
        self.assertEqual(len(self.op.sent), 2)

    def test_restart_after_intent_preserves_uncertainty_and_lock(self):
        service = self.module()
        task = self.active(service)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        target = self.worker.claim_next()
        self.assertIsNotNone(self.worker.begin_dispatch(target.pk, 0, 'apply'))
        self.worker_module.recover_interrupted()
        service.tick()
        history.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(history.status, 2)
        self.assertFalse(task.is_active)
        self.assertTrue(models.PanelLock.objects.exists())
        self.assertFalse(self.worker.run_once())
        self.assertFalse(self.op.sent)

    @skipUnless(connection.vendor == 'mysql', 'Requires production row-lock semantics')
    def test_concurrent_dispatch_has_one_durable_preflight(self):
        service = self.module()
        task = self.active(service)
        barrier, failures = threading.Barrier(2), []
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                service.dispatch(task.pk)
            except Exception as exc:
                failures.append(type(exc).__name__)
            finally:
                close_old_connections()
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(models.PanelTask.objects.count(), 2)
        self.assertEqual(History.objects.filter(task_id=task.pk, status=0).count(), 1)
        self.assertEqual(History.objects.filter(task_id=task.pk, status=3).count(), 1)


class ScheduledUploadTests(TransactionTestCase):
    from apps.baota import test_upload_tasks as upload_fixtures
    setUp = upload_fixtures.UploadTaskTests.setUp
    make_user = upload_fixtures.UploadTaskTests.make_user
    authenticate = upload_fixtures.UploadTaskTests.authenticate
    request = upload_fixtures.UploadTaskTests.request
    group = upload_fixtures.UploadTaskTests.group
    panel = upload_fixtures.UploadTaskTests.panel
    init_upload = upload_fixtures.UploadTaskTests.init_upload
    prepare_upload = upload_fixtures.UploadTaskTests.prepare_upload
    prepared = upload_fixtures.UploadTaskTests.prepared

    def plan(self):
        from . import panel_tasks as service
        self.init_upload()
        source = self.prepared()
        value = service.template(self.admin, source.pk)
        value['acknowledgements'] = ['upload_overwrite']
        task = service.save(self.admin, dict(name='upload', type='panel', trigger='interval', trigger_args=3600,
            panel_plan={key: value[key] for key in ('template_id', 'template_token', 'acknowledgements')}))
        return service, task, source

    def test_uploaded_bundle_remains_while_inactive_schedule_references_it(self):
        from apps.baota import upload_store
        service, task, source = self.plan()
        old = timezone.now() - timedelta(days=10)
        models.PanelTask.objects.filter(pk=source.pk).update(state='completed', updated_at=old)
        source.targets.update(state='completed', updated_at=old)
        models.PanelUploadBundle.objects.filter(pk=self.bundle.pk).update(updated_at=old)
        upload_store.cleanup_expired()
        self.bundle.refresh_from_db()
        self.assertEqual(self.bundle.state, 'sealed')
        task.delete()
        upload_store.cleanup_expired()
        self.bundle.refresh_from_db()
        self.assertEqual(self.bundle.state, 'expired')

    def test_schedule_rechecks_and_uploads_owned_sealed_source(self):
        service, task, source = self.plan()
        service.activate(self.admin, task.pk, True, confirmed=True)
        history = service.dispatch(task.pk)
        self.worker.run_once()
        service.tick()
        while self.worker.run_once():
            service.tick()
        service.tick()
        history.refresh_from_db()
        self.assertEqual(history.status, 1, history.output)
        self.assertEqual(len([c for c in self.remote.calls if c[0] == 'UPLOAD']), 2)

    def test_other_admin_cannot_activate_another_owners_uploads(self):
        service, task, _ = self.plan()
        manager = self.make_user('other-admin', superuser=True)
        with self.assertRaisesRegex(ValueError, '所属账号'):
            service.activate(manager, task.pk, True, confirmed=True)
