"""Queue safety tests. Fixture adapters never contact an actual panel."""
import copy
import importlib
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from django.db import connection, close_old_connections
from django.test import TransactionTestCase

from . import models
from . import tests as legacy


class FixtureClient:
    def __init__(self, panel):
        self.panel = panel

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FixtureOperation:
    code = 'fixture'
    category = 'fixture'
    label = 'Fixture'
    resources = ()

    def __init__(self):
        self.count = 2
        self.sent = []
        self.dispatch_result = {'state': 'success', 'message': 'done', 'evidence': {}}
        self.read_result = {'state': 'success', 'message': 'confirmed', 'evidence': {}}
        self.acks = []
        self.raise_dispatch = False

    def validate(self, params):
        if not isinstance(params, dict) or set(params) - {'all_sites', 'sites'}:
            raise ValueError('invalid fixture params')
        return copy.deepcopy(params)

    def preflight(self, client, params):
        assert not connection.in_atomic_block, 'network inside transaction'
        items = [{'key': str(i), 'label': 'site ' + str(i), 'target': {'id': i + 1},
                  'before': {'panel_version': '13.1.0'},
                  'plan': {'required_acknowledgements': self.acks[:]},
                  'state': 'ready', 'message': 'ready'} for i in range(self.count)]
        return {'items': items, 'summary': {'total': len(items)}}

    def dispatch(self, client, item, phase='apply', remote=None):
        assert not connection.in_atomic_block, 'network inside transaction'
        target = models.PanelTaskTarget.objects.get(panel_id=client.panel.pk, state='dispatching')
        record = next(c for c in target.children if c['snapshot']['key'] == item['key'])
        assert record['state'] == 'dispatching'
        assert record['intents'][-1]['phase'] == phase
        assert models.PanelLock.objects.filter(panel_id=client.panel.pk, target=target).exists()
        self.sent.append((client.panel.pk, item['key'], phase))
        if self.raise_dispatch:
            raise RuntimeError('access_token=DO-NOT-STORE raw-secret')
        return copy.deepcopy(self.dispatch_result)

    def reconcile(self, client, item, remote):
        assert not connection.in_atomic_block, 'network inside transaction'
        return copy.deepcopy(self.read_result)


class PanelTaskTests(TransactionTestCase):
    setUp = legacy.BaotaAPITests.setUp
    make_user = legacy.BaotaAPITests.make_user
    authenticate = legacy.BaotaAPITests.authenticate
    request = legacy.BaotaAPITests.request
    group = legacy.BaotaAPITests.group
    panel = legacy.BaotaAPITests.panel

    def services(self):
        self.service = importlib.import_module('apps.baota.task_service')
        self.worker_module = importlib.import_module('apps.baota.worker')
        self.op = FixtureOperation()
        self.addCleanup(patch.stopall)
        patch('apps.baota.task_service.get_operation', return_value=self.op).start()
        patch('apps.baota.worker.get_operation', return_value=self.op).start()
        self.worker = self.worker_module.PanelWorker(client_factory=FixtureClient)

    def configured(self, **changes):
        record = self.panel(api_base_url='https://api.example.com', api_key='fixture-key', **changes)
        models.Panel.objects.filter(pk=record['id']).update(api_checked_at='2026-10-22 10:00:00')
        return record

    def prepare(self, panel=None, **changes):
        panel = panel or self.configured()
        data = {'operation': 'fixture', 'panel_ids': [panel['id']], 'params': {'all_sites': True}}
        data.update(changes)
        return self.service.prepare(self.admin, data)

    def prepared(self, **changes):
        task = self.prepare(**changes)
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(task.state, 'prepared')
        return task

    def start(self, task, acks=None):
        return self.service.start(self.admin, task.pk, {'acknowledgements': acks or []})

    def test_durable_models_exist(self):
        for name in ('PanelTask', 'PanelTaskTarget', 'PanelLock'):
            self.assertTrue(hasattr(models, name), name + ' durable queue model missing')

    def test_prepare_deduplicates_descendant_selection_and_freezes_panel_names(self):
        self.services()
        root = self.group('root')
        nested = self.group('nested', root['id'])
        panel = self.configured(group_id=nested['id'])
        task = self.prepare(panel, panel_ids=[panel['id'], panel['id']], group_ids=[root['id']])
        self.assertEqual(task.targets.count(), 1)
        target = task.targets.get()
        models.Panel.objects.filter(pk=panel['id']).update(name='later')
        self.assertEqual(target.panel_snapshot['name'], panel['name'])
        self.assertEqual(self.op.sent, [])
        self.assertEqual(task.state, 'preflight_queued')

    def test_start_accepts_only_task_identity_and_acks_and_does_not_expand_again(self):
        self.services()
        task = self.prepared()
        self.op.count = 8
        with self.assertRaises(ValueError):
            self.service.start(self.admin, task.pk, {'params': {'all_sites': True}})
        self.start(task)
        for _ in range(4):
            self.worker.run_once()
        self.assertEqual(len(self.op.sent), 2)
        self.assertEqual(len(task.targets.get().children), 2)

    def test_execution_permission_and_owner_are_both_required(self):
        self.services()
        task = self.prepared()
        other = self.make_user('other')
        other.has_perms = lambda codes: bool(set(codes) & {'exec.panel.execute'})
        with self.assertRaises(PermissionError):
            self.service.start(other, task.pk, {'acknowledgements': []})
        manager = self.make_user('manager')
        manager.has_perms = lambda codes: bool(set(codes) & {'exec.panel.manage'})
        with self.assertRaises(PermissionError):
            self.service.start(manager, task.pk, {'acknowledgements': []})

    def test_required_acknowledgements_are_exact_and_persisted_before_dispatch(self):
        self.services()
        self.op.acks = ['ssl_panel_sync']
        task = self.prepared()
        for codes in ([], ['unexpected'], ['ssl_panel_sync', 'unexpected']):
            with self.assertRaises(ValueError):
                self.start(task, codes)
        self.start(task, ['ssl_panel_sync'])
        task.refresh_from_db()
        self.assertEqual(task.acknowledgements, ['ssl_panel_sync'])
        self.worker.run_once()
        self.assertEqual(len(self.op.sent), 1)

    def test_revision_change_invalidates_start(self):
        self.services()
        task = self.prepared()
        models.Panel.objects.update(api_config_revision=20)
        with self.assertRaises(ValueError):
            self.start(task)
        self.assertEqual(self.op.sent, [])

    def test_revision_change_blocks_each_later_dispatch(self):
        self.services()
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        models.Panel.objects.update(api_config_revision=20)
        self.worker.run_once()
        self.assertEqual(len(self.op.sent), 1)

    def test_claim_is_single_owner_and_same_panel_serial_across_tasks(self):
        self.services()
        panel = self.configured()
        first, second = self.prepared(panel=panel), self.prepared(panel=panel)
        self.start(first)
        self.start(second)
        claimed = self.worker.claim_next()
        self.assertEqual(claimed.task_id, first.pk)
        self.assertIsNone(self.worker.claim_next())
        self.assertEqual(models.PanelLock.objects.count(), 1)

    def test_unrelated_panels_can_be_claimed_in_parallel(self):
        self.services()
        first = self.prepared()
        second = self.prepared(panel=self.configured(name='second', group_id=models.PanelGroup.objects.first().pk))
        self.start(first)
        self.start(second)
        self.assertNotEqual(self.worker.claim_next().panel_id, self.worker.claim_next().panel_id)

    def test_cancel_only_undispatched_children_retains_remote_and_lock(self):
        self.services()
        self.op.dispatch_result = {'state': 'remote', 'message': 'pending', 'remote': {'phase': 'apply'}, 'evidence': {}}
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        self.service.cancel(self.admin, task.pk)
        children = task.targets.get().children
        self.assertEqual([c['state'] for c in children], ['remote', 'cancelled'])
        self.assertEqual(models.PanelLock.objects.count(), 1)

    def test_unknown_exception_after_intent_is_redacted_uncertain_and_never_resent(self):
        self.services()
        self.op.raise_dispatch = True
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        for _ in range(3):
            self.worker.run_once()
        target = task.targets.get()
        self.assertEqual(target.children[0]['state'], 'uncertain')
        self.assertEqual(len(self.op.sent), 1)
        self.assertNotIn('raw-secret', json.dumps(target.children))
        self.assertEqual(models.PanelLock.objects.count(), 1)

    def test_restart_turns_inflight_into_uncertain_and_preserves_lock(self):
        self.services()
        task = self.prepared()
        self.start(task)
        target = self.worker.claim_next()
        self.worker.begin_dispatch(target.pk, 0, 'apply')
        self.worker_module.recover_interrupted()
        target.refresh_from_db()
        self.assertEqual(target.children[0]['state'], 'uncertain')
        self.assertEqual(models.PanelLock.objects.count(), 1)
        self.assertIsNone(self.worker.claim_next())

    def test_restart_retries_readonly_preflight(self):
        self.services()
        task = self.prepare()
        self.worker.claim_next()
        self.worker_module.recover_interrupted()
        self.assertTrue(self.worker.run_once())
        task.refresh_from_db()
        self.assertEqual(task.state, 'prepared')

    def test_ambiguous_reconcile_never_auto_deploys_or_clears_uncertainty(self):
        self.services()
        self.op.dispatch_result = {'state': 'uncertain', 'message': 'unknown', 'evidence': {},
                                   'remote': {'ambiguous': True, 'phase': 'apply'}}
        self.op.read_result = {'state': 'remote', 'message': 'issued', 'evidence': {},
                              'remote': {'next_action': 'deploy', 'certificate': {'fingerprint': 'a'}}}
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        self.service.reconcile(self.admin, task.pk)
        self.worker.run_once()
        target = task.targets.get()
        self.assertEqual(target.children[0]['state'], 'uncertain')
        self.assertEqual(len(self.op.sent), 1)

    def test_resolve_requires_evidence_and_rejects_active_call(self):
        self.services()
        task = self.prepared()
        self.start(task)
        target = self.worker.claim_next()
        self.worker.begin_dispatch(target.pk, 0, 'apply')
        data = {'target_id': target.pk, 'child_key': '0', 'outcome': 'failed',
                'reason': 'manually inspected', 'evidence': 'verified server configuration'}
        with self.assertRaises(ValueError):
            self.service.resolve(self.admin, task.pk, data)
        self.worker_module.recover_interrupted()
        with self.assertRaises(ValueError):
            self.service.resolve(self.admin, task.pk, {**data, 'evidence': ''})
        self.service.resolve(self.admin, task.pk, data)
        target.refresh_from_db()
        self.assertEqual(len(target.children[0]['intents']), 1)
        self.assertTrue(target.children[0]['resolutions'])

    def test_retry_is_new_linked_preflight_and_does_not_bypass_old_lock(self):
        self.services()
        self.op.raise_dispatch = True
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        retry = self.service.retry(self.admin, task.pk)
        self.assertNotEqual(task.pk, retry.pk)
        self.assertEqual(retry.retry_parent_id, task.pk)
        self.worker.run_once()
        self.start(retry)
        self.assertIsNone(self.worker.claim_next())

    def test_selection_api_is_safe_and_catalog_has_374_metadata_entries(self):
        self.services()
        self.configured()
        user = self.make_user('viewer')
        user.roles.create(name='task viewer', page_perms={'exec': {'panel': ['view', 'create']}})
        self.authenticate(user)
        options = self.request('get', '/baota/task-options/')
        encoded = json.dumps(options)
        for key in ('password', 'username', 'ciphertext', 'access_token', 'secure-entry'):
            self.assertNotIn(key, encoded)
        self.request('get', '/baota/', success=False)
        catalog = self.request('get', '/baota/catalog/')
        self.assertEqual(catalog['total'], 374)

    def test_panel_delete_is_protected_by_task_history(self):
        self.services()
        task = self.prepare()
        self.request('delete', '/baota/', {'id': task.targets.get().panel_id}, success=False)
        self.assertEqual(models.Panel.objects.count(), 1)

    def test_limits_and_cross_panel_mapping_rejected(self):
        self.services()
        panel = self.configured()
        for changes in ({'panel_ids': list(range(1, 52))},
                        {'params_by_panel': {'9999': {}}, 'params': {}},
                        {'url': '/arbitrary'}, {'params': {'script': 'id'}}):
            with self.assertRaises(ValueError):
                self.prepare(panel, **changes)

    def test_unconfigured_panel_is_a_per_target_preflight_error(self):
        self.services()
        valid = self.configured()
        invalid = self.panel(name='missing api', group_id=models.PanelGroup.objects.first().pk)
        task = self.prepare(valid, panel_ids=[valid['id'], invalid['id']])
        self.worker.run_once()
        self.worker.run_once()
        states = dict(task.targets.values_list('panel_id', 'state'))
        self.assertEqual(states, {valid['id']: 'prepared', invalid['id']: 'failed'})
        self.start(task)
        self.worker.run_once()
        self.assertEqual(len(self.op.sent), 1)

    def test_success_releases_lock_after_all_children_terminal(self):
        self.services()
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        self.assertTrue(models.PanelLock.objects.exists())
        self.worker.run_once()
        self.assertFalse(models.PanelLock.objects.exists())
        task.refresh_from_db()
        self.assertEqual(task.state, 'completed')

    def test_each_deploy_phase_is_saved_before_dispatch_and_never_repeated(self):
        self.services()
        self.op.count = 1
        self.op.dispatch_result = {'state': 'remote', 'message': 'issued', 'evidence': {},
                                   'remote': {'next_action': 'deploy', 'certificate': {'fingerprint': 'a'}}}
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        self.op.dispatch_result = {'state': 'success', 'message': 'deployed', 'evidence': {}}
        self.worker.run_once()
        self.worker.run_once()
        self.assertEqual([c[2] for c in self.op.sent], ['apply', 'deploy'])
        intents = task.targets.get().children[0]['intents']
        self.assertEqual([i['phase'] for i in intents], ['apply', 'deploy'])

    def test_pending_poll_timeout_keeps_lock_and_never_resends(self):
        self.services()
        self.op.count = 1
        pending = {'state': 'remote', 'message': 'pending', 'evidence': {}, 'remote': {'phase': 'apply'}}
        self.op.dispatch_result = self.op.read_result = pending
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        self.service.reconcile(self.admin, task.pk)
        with patch('apps.baota.worker.MAX_POLLS', 1):
            self.worker.run_once()
        self.assertEqual(task.targets.get().children[0]['state'], 'uncertain')
        self.assertTrue(models.PanelLock.objects.exists())
        self.assertEqual(len(self.op.sent), 1)

    def test_restart_pending_remote_disables_automatic_deploy(self):
        self.services()
        self.op.count = 1
        self.op.dispatch_result = {'state': 'remote', 'message': 'issued', 'evidence': {},
                                   'remote': {'next_action': 'deploy'}}
        task = self.prepared()
        self.start(task)
        self.worker.run_once()
        self.worker_module.recover_interrupted()
        self.assertIsNone(self.worker.claim_next())
        self.assertEqual(task.targets.get().children[0]['state'], 'uncertain')

    def test_resource_view_releases_atomic_request_and_does_not_leak_transport_errors(self):
        self.services()
        panel = self.configured()
        atomic = connection.settings_dict.get('ATOMIC_REQUESTS', False)
        connection.settings_dict['ATOMIC_REQUESTS'] = True
        try:
            def reader(*args, **kwargs):
                self.assertFalse(connection.in_atomic_block)
                raise RuntimeError('access_token=raw-transport-secret')
            with patch('apps.baota.task_views.PanelClient', FixtureClient), patch('apps.baota.task_views.get_resource', reader):
                response = self.client.get('/baota/resources/', {'panel_id': panel['id'], 'kind': 'sites'})
            self.assertTrue(response.json()['error'])
            self.assertNotIn('raw-transport-secret', response.content.decode())
            with patch('apps.baota.task_views.PanelClient', FixtureClient), patch(
                    'apps.baota.task_views.get_resource', side_effect=ValueError('access_token=raw-transport-secret')):
                response = self.client.get('/baota/resources/', {'panel_id': panel['id'], 'kind': 'sites'})
            self.assertNotIn('raw-transport-secret', response.content.decode())
        finally:
            connection.settings_dict['ATOMIC_REQUESTS'] = atomic

    def test_api_end_to_end_uses_real_basedir_adapter_and_frozen_selection(self):
        from .test_operations import PanelFixture
        from .worker import PanelWorker
        protocol = PanelFixture()
        class Client(FixtureClient):
            def request(self, *args, **kwargs):
                return protocol.request(*args, **kwargs)
        worker = PanelWorker(client_factory=Client)
        panel = self.configured()
        created = self.request('post', '/baota/tasks/', {'operation': 'site_basedir',
                               'panel_ids': [panel['id']], 'params': {'all_sites': True, 'enabled': True}})
        self.assertEqual(created['state'], 'preflight_queued')
        worker.run_once()
        preview = self.request('get', '/baota/tasks/' + str(created['id']) + '/')
        self.assertEqual(len(preview['targets'][0]['children']), 1)
        protocol.sites.append({**protocol.sites[0], 'id': 2, 'name': 'later.example.com'})
        self.request('post', '/baota/tasks/' + str(created['id']) + '/start/', {'acknowledgements': []})
        worker.run_once()
        detail = self.request('get', '/baota/tasks/' + str(created['id']) + '/')
        self.assertEqual(detail['targets'][0]['children'][0]['state'], 'success')
        self.assertEqual([c[1] for c in protocol.calls if c[3]], ['multiple_basedir'])
        self.assertNotIn('SECRET', json.dumps(detail))

    def test_process_file_lock_excludes_a_second_coordinator(self):
        from django.core.management.base import CommandError
        from .management.commands.runpanelworker import ProcessLock
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.lock'
            with ProcessLock(path):
                with self.assertRaises(CommandError):
                    with ProcessLock(path):
                        self.fail('second process lock accepted')
            with ProcessLock(path):
                pass

    def test_mariadb_concurrent_claim_uses_database_authority(self):
        if connection.vendor != 'mysql':
            self.skipTest('MariaDB integration check; SQLite cannot prove row-lock concurrency')
        self.services()
        panel = self.configured()
        self.queue_concurrent_fixture(panel)
        barrier, results, errors = threading.Barrier(2), [], []
        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                target = self.worker_module.PanelWorker(client_factory=FixtureClient).claim_next()
                results.append(target.pk if target else None)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                connection.close()
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertEqual(models.PanelLock.objects.count(), 1)

    def queue_concurrent_fixture(self, panel):
        # Complete both readonly preflights before either task becomes dispatchable.
        # run_once follows the durable FIFO queue, not the task just created by a helper.
        tasks = [self.prepared(panel=panel) for _ in range(2)]
        for task in tasks:
            self.start(task)
        return tasks

    def test_concurrency_fixture_queues_both_tasks_without_dispatch(self):
        self.services()
        tasks = self.queue_concurrent_fixture(self.configured())
        self.assertEqual(list(models.PanelTaskTarget.objects.filter(
            task__in=tasks).values_list('state', flat=True)), ['queued', 'queued'])
        self.assertEqual(self.op.sent, [])
        self.assertFalse(models.PanelLock.objects.exists())

    def test_stopped_worker_does_not_claim_new_work(self):
        self.services()
        task = self.prepare()
        self.worker.stop_event.set()
        self.assertIsNone(self.worker.claim_next())
        self.assertEqual(task.targets.get().state, 'preflight_queued')

    def test_shutdown_interrupts_between_readonly_calls_and_requeues_preflight(self):
        self.services()
        calls = []
        class Client(FixtureClient):
            def request(self, path):
                calls.append(path)
                return {}
        self.worker.client_factory = Client
        def preflight(client, params):
            client.request('/first')
            self.worker.stop_event.set()
            client.request('/must-not-run')
            return {'items': [], 'summary': {}}
        self.op.preflight = preflight
        task = self.prepare()
        self.worker.run_once()
        self.assertEqual(calls, ['/first'])
        self.assertEqual(task.targets.get().state, 'preflight_queued')

    def test_old_uncertain_targets_do_not_starve_new_preflight(self):
        self.services()
        panel = self.configured()
        tasks = models.PanelTask.objects.bulk_create([
            models.PanelTask(operation='fixture', owner=self.admin, state='uncertain') for _ in range(1000)])
        models.PanelTaskTarget.objects.bulk_create([
            models.PanelTaskTarget(task=task, panel_id=panel['id'], state='uncertain') for task in tasks])
        fresh = self.prepare(panel)
        claimed = self.worker.claim_next()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.task_id, fresh.pk)
