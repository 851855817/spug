"""Durable panel-boundary batching; adapters never contact a real panel."""
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from . import models, task_service as s
from . import test_tasks as fixtures
from . import test_upload_tasks as upload_fixtures


class PanelBatchTests(TransactionTestCase):
    setUp = fixtures.PanelTaskTests.setUp
    make_user = fixtures.PanelTaskTests.make_user
    authenticate = fixtures.PanelTaskTests.authenticate
    request = fixtures.PanelTaskTests.request
    group = fixtures.PanelTaskTests.group
    panel = fixtures.PanelTaskTests.panel
    configured = fixtures.PanelTaskTests.configured
    services = fixtures.PanelTaskTests.services
    prepare = fixtures.PanelTaskTests.prepare

    def prepared_panels(self, count=3):
        self.services()
        self.op.count = 1
        group = self.group('batch')['id']
        panels = [self.configured(name='batch ' + str(i), group_id=group) for i in range(count)]
        task = self.prepare(panel=panels[0], panel_ids=[p['id'] for p in panels])
        for _ in panels:
            self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(task.state, 'prepared')
        return task

    def start_batch(self, task, size=1, threshold=1):
        return s.start(self.admin, task.pk, {'acknowledgements': [], 'execution_policy': {
            'batch_size': size, 'failure_threshold': threshold}})

    def test_batch_policy_is_optional_and_strictly_validated(self):
        task = self.prepared_panels()
        for policy in (None, {}, {'batch_size': True, 'failure_threshold': 1},
                       {'batch_size': 11, 'failure_threshold': 1},
                       {'batch_size': 1, 'failure_threshold': 2},
                       {'batch_size': 2, 'failure_threshold': 0},
                       {'batch_size': 2, 'failure_threshold': 1, 'index': 2}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                s.start(self.admin, task.pk, {'acknowledgements': [], 'execution_policy': policy})
        s.start(self.admin, task.pk, {'acknowledgements': []})
        task.refresh_from_db()
        self.assertEqual(task.execution_policy, {})
        self.assertFalse(s.task_view(task)['batch']['enabled'])
        for _ in range(3):
            self.assertTrue(self.worker.run_once())
        self.assertEqual(len(self.op.sent), 3)

    def test_successful_batch_pauses_durably_until_explicit_continue(self):
        task = self.prepared_panels()
        self.start_batch(task)
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(task.state, 'paused')
        batch = s.task_view(task)['batch']
        self.assertEqual((batch['index'], batch['total'], batch['state'], batch['pending']), (1, 3, 'paused', 2))
        self.assertFalse(self.worker.run_once())
        s.refresh_task(task)
        s.reconcile(self.admin, task.pk)
        self.assertFalse(self.worker.run_once())
        resumed = s.continue_batch(self.admin, task.pk, {})
        self.assertEqual(s.task_view(resumed)['batch']['index'], 2)
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())
        s.continue_batch(self.admin, task.pk, {})
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(task.state, 'completed')
        self.assertEqual(len(self.op.sent), 3)

    def test_later_batch_is_ineligible_until_every_current_target_finishes(self):
        task = self.prepared_panels()
        self.start_batch(task, size=2)
        first, second = self.worker.claim_next(), self.worker.claim_next()
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(self.worker.claim_next())
        self.worker.process_target(second.pk)
        self.assertIsNone(self.worker.claim_next())
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, {})
        self.worker.process_target(first.pk)
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['state'], 'paused')

    def test_threshold_trips_before_queued_target_and_ack_resumes_without_retry(self):
        task = self.prepared_panels()
        self.start_batch(task, size=2)
        self.op.dispatch_result = {'state': 'failed', 'message': 'fixture failed', 'evidence': {}}
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['state'], 'tripped')
        self.assertFalse(self.worker.run_once())
        for data in ({}, {'acknowledge': False}, {'acknowledge': 1}):
            with self.assertRaises(ValueError):
                s.continue_batch(self.admin, task.pk, data)
        s.continue_batch(self.admin, task.pk, {'acknowledge': True})
        self.op.dispatch_result = {'state': 'success', 'message': 'done', 'evidence': {}}
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())
        self.assertEqual(len(self.op.sent), 2)
        self.assertEqual(len({p for p, _, _ in self.op.sent}), 2)

    def test_failure_below_threshold_still_requires_manual_next_batch(self):
        task = self.prepared_panels()
        self.start_batch(task, size=2, threshold=2)
        self.op.dispatch_result = {'state': 'failed', 'message': 'fixture failed', 'evidence': {}}
        self.assertTrue(self.worker.run_once())
        self.op.dispatch_result = {'state': 'success', 'message': 'done', 'evidence': {}}
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['state'], 'paused')
        self.assertFalse(self.worker.run_once())

    def test_remote_holds_locks_and_never_automatically_dispatches_deploy(self):
        task = self.prepared_panels()
        self.start_batch(task, size=2, threshold=2)
        self.op.dispatch_result = {'state': 'remote', 'message': 'remote', 'evidence': {},
                                   'remote': {'next_action': 'deploy'}}
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['state'], 'tripped')
        self.assertEqual(models.PanelLock.objects.count(), 1)
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, {'acknowledge': True})
        s.reconcile(self.admin, task.pk)
        self.assertTrue(self.worker.run_once())
        self.assertEqual(len(self.op.sent), 1)
        self.assertFalse(self.worker.run_once())
        task.refresh_from_db()
        self.assertNotIn('失败达到阈值', s.task_view(task)['batch']['pause_reason'])

    def test_uncertain_and_interrupted_calls_hold_until_resolved(self):
        task = self.prepared_panels()
        self.start_batch(task)
        self.op.raise_dispatch = True
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())
        task.refresh_from_db()
        target = task.targets.first()
        self.assertEqual(s.task_view(task)['batch']['state'], 'tripped')
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, {'acknowledge': True})
        s.resolve(self.admin, task.pk, {'target_id': target.pk, 'child_key': '0', 'outcome': 'failed',
                                       'reason': 'verified', 'evidence': 'fixture inspection'})
        s.continue_batch(self.admin, task.pk, {'acknowledge': True})
        self.assertEqual(len(self.op.sent), 1)
        self.op.raise_dispatch = False
        self.assertTrue(self.worker.run_once())
        self.assertEqual(len(self.op.sent), 2)

    def test_preflight_failures_and_empty_terminal_targets_do_not_consume_batch_slots(self):
        task = self.prepared_panels(4)
        failed, empty = list(task.targets.all())[:2]
        failed.state, failed.children = 'failed', []
        failed.save(update_fields=['state', 'children'])
        empty.children = []
        empty.save(update_fields=['children'])
        self.start_batch(task)
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual((s.task_view(task)['batch']['total'], s.task_view(task)['batch']['pending']), (2, 1))

    def test_cancel_while_paused_cancels_remaining_work_without_dispatch(self):
        task = self.prepared_panels()
        self.start_batch(task)
        self.worker.run_once()
        s.cancel(self.admin, task.pk)
        self.assertFalse(self.worker.run_once())
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, {})
        self.assertEqual(len(self.op.sent), 1)

    def test_continue_http_requires_execute_and_owner_and_uses_its_own_transaction(self):
        task = self.prepared_panels()
        self.start_batch(task)
        self.worker.run_once()
        self.authenticate(self.make_user('viewer', perms=['exec.panel.view']))
        self.request('post', '/baota/tasks/%s/continue/' % task.pk, success=False)
        self.authenticate(self.admin)
        observed = []
        original = s.continue_batch
        def check_transaction(*args, **kwargs):
            observed.append(connection.in_atomic_block)
            return original(*args, **kwargs)
        atomic = connection.settings_dict.get('ATOMIC_REQUESTS', False)
        connection.settings_dict['ATOMIC_REQUESTS'] = True
        try:
            with patch.object(s, 'continue_batch', side_effect=check_transaction):
                result = self.request('post', '/baota/tasks/%s/continue/' % task.pk)
        finally:
            connection.settings_dict['ATOMIC_REQUESTS'] = atomic
        self.assertEqual(observed, [False])
        self.assertEqual(result['batch']['index'], 2)

    def test_continue_reclassifies_results_and_locks_task_before_targets(self):
        task = self.prepared_panels()
        self.start_batch(task)
        self.worker.run_once()
        target = task.targets.first()
        target.children[0]['state'] = 'failed'
        target.save(update_fields=['children'])
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, {})
        with CaptureQueriesContext(connection) as queries:
            s.continue_batch(self.admin, task.pk, {'acknowledge': True})
        selects = [q['sql'].lower() for q in queries if q['sql'].lower().startswith('select')]
        self.assertIn('baota_tasks', selects[0])
        self.assertIn('baota_task_targets', selects[1])
        if connection.vendor == 'mysql':
            self.assertIn('for update', selects[0])
            self.assertIn('for update', selects[1])

    def test_final_failure_finishes_with_failure_evidence_and_no_stuck_pause(self):
        task = self.prepared_panels(1)
        self.start_batch(task)
        self.op.dispatch_result = {'state': 'failed', 'message': 'fixture failed', 'evidence': {}}
        self.worker.run_once()
        task.refresh_from_db()
        self.assertEqual(task.state, 'completed')
        self.assertEqual(s.task_view(task)['batch']['failure_count'], 1)
        self.assertFalse(self.worker.run_once())

    def test_failed_preflight_children_do_not_trip_runnable_target_before_dispatch(self):
        task = self.prepared_panels(2)
        target = task.targets.first()
        target.children.append({'state': 'failed', 'snapshot': {'key': 'blocked', 'state': 'failed'},
                                'intents': [], 'result': {}, 'resolutions': []})
        target.save(update_fields=['children'])
        self.start_batch(task)
        self.assertTrue(self.worker.run_once())
        self.assertEqual(len(self.op.sent), 1)
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['state'], 'paused')

    def test_recovery_marks_interrupted_batch_uncertain_without_admitting_later_targets(self):
        task = self.prepared_panels()
        self.start_batch(task)
        claimed = self.worker.claim_next()
        self.assertIsNotNone(self.worker.begin_dispatch(claimed.pk, 0, 'apply'))
        self.worker_module.recover_interrupted()
        task.refresh_from_db()
        self.assertTrue(s.task_view(task)['batch']['unresolved'])
        self.assertFalse(self.worker.run_once())
        self.assertTrue(models.PanelLock.objects.filter(target_id=claimed.pk).exists())

    def test_late_upload_intent_is_blocked_after_another_target_trips(self):
        from . import upload_runner
        task = self.prepared_panels(2)
        self.start_batch(task, size=2)
        first, second = self.worker.claim_next(), self.worker.claim_next()
        bundle = models.PanelUploadBundle.objects.create(owner=self.admin, file_count=1)
        source = models.PanelUploadFile.objects.create(bundle=bundle, relative_path='fixture.txt', path_digest='f' * 64, size=1)
        row = models.PanelUploadResult.objects.create(target=second, source=source)
        self.op.dispatch_result = {'state': 'failed', 'message': 'fixture failed', 'evidence': {}}
        self.worker.process_target(first.pk)
        with patch.object(upload_runner.s, 'configured', side_effect=AssertionError('write validation reached')):
            self.assertFalse(upload_runner.begin(self.worker, second.pk, row.pk))
        row.refresh_from_db()
        self.assertEqual(row.intent, {})

    def test_upload_file_failure_summary_trips_even_while_other_files_remain_ready(self):
        task = self.prepared_panels(2)
        targets = list(task.targets.all())
        for target in targets:
            target.summary['upload'] = {'failed': 1, 'pending': 2}
            target.save(update_fields=['summary'])
        self.start_batch(task, size=2)
        self.assertIsNotNone(self.worker.claim_next())
        first = task.targets.first()
        first.state = 'queued'
        first.summary['upload']['failed'] = 2
        first.save(update_fields=['state', 'summary'])
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['state'], 'tripped')
        self.assertIsNone(self.worker.claim_next())

    def ssl_remote_task(self, count=2):
        task = self.prepared_panels(count)
        task.operation = 'ssl_apply'
        task.save(update_fields=['operation'])
        self.start_batch(task)
        self.op.dispatch_result = {'state': 'remote', 'message': 'issued, deployment pending', 'evidence': {},
                                   'remote': {'next_action': 'deploy', 'certificate_id': 99}}
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        return task

    def verify_ssl_deploy(self, task):
        self.op.read_result = {'state': 'remote', 'message': 'issued verified', 'evidence': {},
                               'remote': {'next_action': 'deploy', 'certificate_id': 99}}
        s.reconcile(self.admin, task.pk)
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        return {'acknowledge': True, 'deploy': {'target_id': task.targets.first().pk, 'child_key': '0'}}

    def test_ssl_deploy_requires_verified_explicit_phase_confirmation_and_does_not_admit_next_batch(self):
        task = self.ssl_remote_task()
        deploy = {'acknowledge': True, 'deploy': {'target_id': task.targets.first().pk, 'child_key': '0'}}
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)
        self.assertFalse(self.worker.run_once())
        deploy = self.verify_ssl_deploy(task)
        self.assertEqual(s.task_view(task)['batch']['deployable'][0]['child_key'], '0')
        self.assertFalse(self.worker.run_once())
        for data in ({'deploy': deploy['deploy']}, {'acknowledge': False, 'deploy': deploy['deploy']}):
            with self.assertRaises(ValueError):
                s.continue_batch(self.admin, task.pk, data)
        self.op.dispatch_result = {'state': 'success', 'message': 'deployed', 'evidence': {}}
        s.continue_batch(self.admin, task.pk, deploy)
        self.assertTrue(self.worker.run_once())
        self.assertEqual([phase for _, _, phase in self.op.sent], ['apply', 'deploy'])
        self.assertFalse(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(s.task_view(task)['batch']['index'], 1)
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)

    def test_ssl_phase_authorization_cannot_bypass_owner_execute_cancel_or_uncertain(self):
        task = self.ssl_remote_task()
        deploy = self.verify_ssl_deploy(task)
        for username, perms in [('viewer', ['exec.panel.view']), ('other', ['exec.panel.execute'])]:
            user = self.make_user(username)
            user.has_perms = lambda codes, allowed=perms: set(codes).issubset(allowed)
            with self.assertRaises(PermissionError):
                s.continue_batch(user, task.pk, deploy)
        target = task.targets.first()
        for key, value in [('uncertain_provenance', True), ('state', 'uncertain')]:
            original = target.children[0].get(key)
            target.children[0][key] = value
            target.save(update_fields=['children'])
            with self.assertRaises(ValueError):
                s.continue_batch(self.admin, task.pk, deploy)
            if original is None:
                target.children[0].pop(key)
            else:
                target.children[0][key] = original
        target.children[0]['result']['remote']['ambiguous'] = True
        target.save(update_fields=['children'])
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)
        s.cancel(self.admin, task.pk)
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)

    def test_ssl_deploy_fresh_dispatch_rejects_changed_remote_or_repeated_authorization(self):
        task = self.ssl_remote_task()
        deploy = self.verify_ssl_deploy(task)
        s.continue_batch(self.admin, task.pk, deploy)
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)
        target = self.worker.claim_next()
        self.assertIsNotNone(target)
        target.children[0]['result']['remote']['certificate_id'] = 100
        target.save(update_fields=['children'])
        self.assertIsNone(self.worker.begin_dispatch(target.pk, 0, 'deploy'))
        self.assertEqual(len(self.op.sent), 1)
        self.assertTrue(models.PanelLock.objects.filter(target=target).exists())

    def test_ssl_explicit_deploy_failure_remains_held_and_is_never_resent(self):
        task = self.ssl_remote_task()
        deploy = self.verify_ssl_deploy(task)
        s.continue_batch(self.admin, task.pk, deploy)
        self.op.raise_dispatch = True
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())
        task.refresh_from_db()
        self.assertTrue(s.task_view(task)['batch']['unresolved'])
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)
        self.assertEqual([phase for _, _, phase in self.op.sent], ['apply', 'deploy'])

    def test_ssl_phase_confirmation_only_authorizes_one_fixed_child_of_current_batch(self):
        task = self.prepared_panels(3)
        task.operation = 'ssl_apply'
        task.save(update_fields=['operation'])
        self.start_batch(task, size=2)
        first, second = self.worker.claim_next(), self.worker.claim_next()
        self.op.dispatch_result = {'state': 'remote', 'message': 'issued', 'evidence': {},
                                   'remote': {'next_action': 'deploy', 'certificate_id': 99}}
        self.worker.process_target(first.pk)
        # The second already-claimed apply must stop after the first remote hold.
        self.worker.process_target(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.children[0]['intents'], [])
        deploy = self.verify_ssl_deploy(task)
        s.continue_batch(self.admin, task.pk, deploy)
        claimed = self.worker.claim_next()
        self.assertEqual(claimed.pk, first.pk)
        self.assertIsNone(self.worker.claim_next())

    def test_two_concurrent_ssl_results_only_deploy_the_explicitly_confirmed_child(self):
        task = self.prepared_panels(3)
        task.operation = 'ssl_apply'
        task.save(update_fields=['operation'])
        self.start_batch(task, size=2)
        first, second = self.worker.claim_next(), self.worker.claim_next()
        self.worker.begin_dispatch(first.pk, 0, 'apply')
        self.worker.begin_dispatch(second.pk, 0, 'apply')
        issued = {'state': 'remote', 'message': 'issued', 'evidence': {}, 'remote': {'next_action': 'deploy', 'certificate_id': 99}}
        self.worker.save_result(first.pk, 0, issued, dispatched=True)
        self.worker.save_result(second.pk, 0, issued, dispatched=True)
        self.op.read_result = issued
        s.reconcile(self.admin, task.pk)
        self.worker.run_once()
        self.worker.run_once()
        confirm = {'acknowledge': True, 'deploy': {'target_id': first.pk, 'child_key': '0'}}
        s.continue_batch(self.admin, task.pk, confirm)
        claimed = self.worker.claim_next()
        self.assertEqual(claimed.pk, first.pk)
        self.assertIsNone(self.worker.claim_next())
        self.op.dispatch_result = {'state': 'success', 'message': 'deployed', 'evidence': {}}
        self.worker.process_target(first.pk)
        self.assertFalse(self.worker.run_once())
        second.refresh_from_db()
        self.assertEqual([intent['phase'] for intent in second.children[0]['intents']], ['apply'])
        s.continue_batch(self.admin, task.pk, {'acknowledge': True, 'deploy': {'target_id': second.pk, 'child_key': '0'}})
        self.assertTrue(self.worker.run_once())
        self.assertFalse(self.worker.run_once())

    def test_changed_sibling_readback_revokes_pending_deploy_and_requires_new_verification(self):
        task = self.prepared_panels(2)
        task.operation = 'ssl_apply'
        task.save(update_fields=['operation'])
        self.start_batch(task, size=2)
        first, second = self.worker.claim_next(), self.worker.claim_next()
        self.worker.begin_dispatch(first.pk, 0, 'apply')
        self.worker.begin_dispatch(second.pk, 0, 'apply')
        issued = {'state': 'remote', 'message': 'issued', 'evidence': {}, 'remote': {'next_action': 'deploy', 'certificate_id': 99}}
        self.worker.save_result(first.pk, 0, issued, dispatched=True)
        self.worker.save_result(second.pk, 0, issued, dispatched=True)
        self.op.read_result = issued
        s.reconcile(self.admin, task.pk)
        self.worker.run_once()
        self.worker.run_once()
        confirm = {'acknowledge': True, 'deploy': {'target_id': first.pk, 'child_key': '0'}}
        s.continue_batch(self.admin, task.pk, confirm)
        s.resolve(self.admin, task.pk, {'target_id': second.pk, 'child_key': '0', 'outcome': 'failed',
                                      'reason': 'fixture', 'evidence': 'inspection'})
        task.refresh_from_db()
        self.assertFalse(s.task_view(task)['batch']['deployment_pending'])
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, confirm)
        self.assertFalse(self.worker.run_once())
        s.reconcile(self.admin, task.pk)
        self.assertTrue(self.worker.run_once())
        s.continue_batch(self.admin, task.pk, confirm)
        self.op.dispatch_result = {'state': 'success', 'message': 'deployed', 'evidence': {}}
        self.assertTrue(self.worker.run_once())

    def test_ssl_authorization_is_revoked_on_worker_restart_before_claim(self):
        task = self.ssl_remote_task()
        deploy = self.verify_ssl_deploy(task)
        s.continue_batch(self.admin, task.pk, deploy)
        self.worker_module.recover_interrupted()
        task.refresh_from_db()
        self.assertNotIn('deployment', task.execution_policy)
        self.assertFalse(self.worker.run_once())
        with self.assertRaises(ValueError):
            s.continue_batch(self.admin, task.pk, deploy)
        self.assertEqual(len(self.op.sent), 1)

    def test_ssl_deploy_rejects_new_active_call_between_confirmation_and_intent(self):
        task = self.ssl_remote_task(3)
        deploy = self.verify_ssl_deploy(task)
        s.continue_batch(self.admin, task.pk, deploy)
        target = self.worker.claim_next()
        self.assertIsNotNone(target)
        # Any active target in the authorized batch makes the phase ineligible.
        other = task.targets.all()[1]
        other.state = 'running'
        other.save(update_fields=['state'])
        task.execution_policy['batch_size'] = 2
        task.save(update_fields=['execution_policy'])
        self.assertIsNone(self.worker.begin_dispatch(target.pk, 0, 'deploy'))
        self.assertEqual(len(self.op.sent), 1)


class UploadPreflightBatchTests(TransactionTestCase):
    setUp = upload_fixtures.UploadTaskTests.setUp
    make_user = upload_fixtures.UploadTaskTests.make_user
    authenticate = upload_fixtures.UploadTaskTests.authenticate
    request = upload_fixtures.UploadTaskTests.request
    group = upload_fixtures.UploadTaskTests.group
    panel = upload_fixtures.UploadTaskTests.panel
    init_upload = upload_fixtures.UploadTaskTests.init_upload
    prepare_upload = upload_fixtures.UploadTaskTests.prepare_upload

    def test_preflight_file_failure_is_not_counted_as_execution_failure_after_success(self):
        self.init_upload(paths=('a.txt', 'blocked.txt'))
        self.remote.links.add('/www/site/blocked.txt')
        second = self.panel(name='second', group_id=self.record['group_id'], api_base_url='https://example.com', api_key='fixture')
        models.Panel.objects.filter(pk=second['id']).update(api_checked_at='checked', api_version='13.1.0')
        task = self.prepare_upload(panel_ids=[self.record['id'], second['id']])
        self.worker.run_once()
        self.worker.run_once()
        s.start(self.admin, task.pk, {'acknowledgements': ['upload_overwrite'],
            'execution_policy': {'batch_size': 1, 'failure_threshold': 1}})
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        batch = s.task_view(task)['batch']
        self.assertEqual((batch['state'], batch['failure_count'], batch['pending']), ('paused', 0, 1))
        self.assertFalse(self.worker.run_once())
