"""Recovery-only checks; no Docker, database or production paths are accessed."""
import ast
import copy
from contextlib import contextmanager, nullcontext, redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
TABLES = {
    'account_resource_quota_lock', 'account_resource_policies',
    'account_resource_ownerships', 'account_resource_grants',
    'account_resource_group_owners', 'account_resource_audit',
    'alarm_resource_scopes', 'notify_resource_audience', 'deploy_resource_uploads',
}


def recovery(real_deadline=False):
    spec = importlib.util.spec_from_file_location('resource_recovery_tested', HERE / 'recover.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # SIGALRM is Linux-only. Test its implementation separately below, while
    # controller tests retain the real controller and isolate only the clock.
    if not real_deadline: module.deadline = nullcontext
    return module


def initialized_fixture(module):
    rows = {
        'policies': [{'user_id': 2, 'server_limit': 0}], 'locks': [{'id': 1}],
        'audits': [{'id': 1, 'actor_id': 1, 'account_id': 2,
                    'event': 'legacy_quota_initialized', 'detail': {'limit': 0},
                    'created_at': '2026-11-06 22:38:00.123456'}],
    }
    digest = lambda value: module.sha(json.dumps(value, sort_keys=True).encode())
    before = {'schema': {'legacy': 'old-schema'},
              'business': {'legacy': {'count': 3, 'digest': 'old-business'}},
              'migrations': {'account/0001_initial': 'old-migration'}}
    after = copy.deepcopy(before)
    after['schema'].update({table: digest(table) for table in TABLES})
    values = {'account_resource_policies': rows['policies'],
              'account_resource_quota_lock': rows['locks'], 'account_resource_audit': rows['audits']}
    after['business'].update({table: {'count': len(values.get(table, [])),
                                    'digest': digest(values.get(table, []))} for table in TABLES})
    after['migrations'].update({app + '/0002_account_resources': 'new-' + app
                                for app in ('account', 'alarm', 'notify', 'deploy')})
    proof = {'new_schema': {table: after['schema'][table] for table in TABLES}}
    account = {'id': 1, 'active': True, 'admin': True, 'host_ids': [], 'panel_ids': [],
               'used': 0, 'has_policy': False, 'limit': None, 'role_ids': [],
               'legacy_group_ids': [], 'legacy_group_hosts_requiring_explicit_assignment': []}
    impact = {'mode': 'report', 'accounts': [account, dict(account, id=2, admin=False)],
              'resources': {'hosts': [], 'panels': [], 'ownerships': [], 'links': [],
                            'ordinary_creator_hosts_without_independent_key': []},
              'schedules': [], 'in_flight': {key: [] for key in (
                  'schedule_history_ids', 'panel_task_ids', 'ops_run_ids', 'release_ids',
                  'build_ids', 'pipeline_history_ids')}}
    current = copy.deepcopy(impact)
    current['accounts'][1].update(has_policy=True, limit=0)
    return [before, after, proof, impact, current, rows, TABLES]


class InitializationEvidenceTests(unittest.TestCase):
    def test_exact_initialized_rows_derive_empty_additions_without_mutating_evidence(self):
        module = recovery()
        args = initialized_fixture(module)
        original = copy.deepcopy(args)
        derived = module.validate_initialization(*args)
        self.assertEqual(args, original)
        self.assertEqual(derived['schema'], args[1]['schema'])
        self.assertEqual(derived['migrations'], args[1]['migrations'])
        self.assertEqual(derived['business']['legacy'], args[0]['business']['legacy'])
        for table in TABLES:
            self.assertEqual(derived['business'][table],
                             {'count': 0, 'digest': module.sha(b'[]')})

    def test_extra_or_changed_policy_lock_and_audit_rows_are_rejected(self):
        module = recovery()
        changes = [
            lambda rows: rows['policies'].append({'user_id': 3, 'server_limit': 0}),
            lambda rows: rows['policies'][0].update(server_limit=None),
            lambda rows: rows['policies'][0].update(user_id=1),
            lambda rows: rows['locks'].append({'id': 2}),
            lambda rows: rows['locks'][0].update(id=2),
            lambda rows: rows['audits'].append(copy.deepcopy(rows['audits'][0])),
        ]
        for key, value in (('id', 2), ('actor_id', 2), ('account_id', 1),
                           ('event', 'other'), ('detail', {'limit': 1}),
                           ('detail', {'limit': 0, 'other': True}), ('unexpected', 'value')):
            changes.append(lambda rows, key=key, value=value: rows['audits'][0].update({key: value}))
        for index, change in enumerate(changes):
            args = initialized_fixture(module)
            change(args[5])
            with self.subTest(index=index), self.assertRaises(ValueError):
                module.validate_initialization(*args)

    def test_audit_timestamp_is_naive_and_within_the_measured_interruption_window(self):
        module = recovery()
        for timestamp in ('2026-11-06 22:37:10.999999', '2026-11-06 22:40:00',
                          '2026-11-05 22:38:00', '2026-11-06 22:38:00+08:00',
                          '2026-11-06 14:38:00Z', 'not-a-date', ''):
            args = initialized_fixture(module)
            args[5]['audits'][0]['created_at'] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                module.validate_initialization(*args)
        for timestamp in ('2026-11-06 22:37:11', '2026-11-06 22:39:59.999999'):
            args = initialized_fixture(module)
            args[5]['audits'][0]['created_at'] = timestamp
            with self.subTest(timestamp=timestamp):
                module.validate_initialization(*args)

    def test_structure_legacy_business_and_each_addition_count_are_preserved(self):
        module = recovery()
        changes = [
            lambda args: args[1]['schema'].update(legacy='changed'),
            lambda args: args[1]['schema'].update(unexpected='new-table'),
            lambda args: args[1]['schema'].pop('legacy'),
            lambda args: args[1]['schema'].update(account_resource_policies='changed'),
            lambda args: args[1]['business']['legacy'].update(digest='changed'),
            lambda args: args[1]['business']['legacy'].update(count=4),
        ]
        for table in TABLES:
            changes.append(lambda args, table=table: args[1]['business'][table].update(count=5))
        for index, change in enumerate(changes):
            args = initialized_fixture(module)
            change(args)
            with self.subTest(index=index), self.assertRaises((ValueError, KeyError)):
                module.validate_initialization(*args)

    def test_impact_may_only_add_the_one_previously_missing_policy(self):
        module = recovery()
        changes = [
            lambda args: args[3]['accounts'][1].update(id=3),
            lambda args: args[3]['accounts'][1].update(used=1),
            lambda args: args[3]['accounts'][1].update(has_policy=True),
            lambda args: args[3]['accounts'].append(dict(args[3]['accounts'][1], id=3)),
            lambda args: args[4]['accounts'][0].update(limit=0),
            lambda args: args[4]['accounts'][1].update(host_ids=[7]),
            lambda args: args[4]['resources']['hosts'].append({'id': 7, 'created_by_id': 2}),
            lambda args: args[4]['in_flight']['ops_run_ids'].append(3),
        ]
        for index, change in enumerate(changes):
            args = initialized_fixture(module)
            change(args)
            with self.subTest(index=index), self.assertRaises(ValueError):
                module.validate_initialization(*args)


class ClosedHost:
    """Stateful external boundary: controller actions change ingress/worker state."""
    def __init__(self, failures=(), guard=None):
        self.failures, self.guard = set(failures), guard
        self.events, self.counts = [], {}
        self.closed, self.backend_running, self.health_running = True, False, False
        self.recovery_evidence = {'verified': True}
        self.stage = Path('/unused-test-stage')
        self.recorded = []
        self.snapshot = {}

    def action(self, name):
        if self.guard: self.guard()
        self.events.append(name)
        self.counts[name] = self.counts.get(name, 0) + 1
        if name in self.failures or '%s:%s' % (name, self.counts[name]) in self.failures:
            raise RuntimeError('sensitive exception detail')

    def save(self, name, value): self.action('save:' + name)
    def impact(self): self.action('impact'); return {'mode': 'report'}
    def fingerprint(self): self.action('fingerprint'); return copy.deepcopy(self.snapshot)
    def overlay_ui(self): self.action('ui')
    def verify_candidate(self, *args): self.action('verify-candidate')
    def compose_candidate(self): self.action('compose')
    def start_candidate(self): self.action('start'); self.backend_running = True
    def open(self): self.action('open'); self.closed = False
    def verify_open(self, *args): self.action('verify-open')
    def resume_health(self): self.action('resume-health'); self.health_running = True
    def close(self):
        self.action('close')
        self.closed, self.backend_running, self.health_running = True, False, False
    def record(self, report):
        self.action('record')
        self.recorded.append(copy.deepcopy(report))
    def migrate(self): self.action('FORBIDDEN-migrate')
    def initialize(self, *args): self.action('FORBIDDEN-initialize')
    def overlay_api(self): self.action('FORBIDDEN-api')
    def restore(self, *args, **kwargs): self.action('FORBIDDEN-old-code-restore')


class RecoveryContinuationTests(unittest.TestCase):
    def assert_no_repeated_destructive_work(self, host):
        self.assertFalse(any(name.startswith('FORBIDDEN-') for name in host.events))
        self.assertLessEqual(host.counts.get('ui', 0), 1)
        self.assertLessEqual(host.counts.get('compose', 0), 1)
        self.assertLessEqual(host.counts.get('start', 0), 1)

    def test_success_only_continues_ui_compose_and_start_after_verified_evidence(self):
        host = ClosedHost()
        host.snapshot = {'after': True}
        result = recovery().continue_candidate(host, {'before': True}, {'after': True})
        self.assertTrue(result['published'])
        self.assertFalse(result['maintenance_closed'])
        self.assertFalse(host.closed)
        self.assertTrue(host.backend_running and host.health_running)
        self.assertLess(host.events.index('save:resource-recovery-evidence.json'), host.events.index('ui'))
        self.assertLess(host.events.index('verify-candidate'), host.events.index('compose'))
        self.assertEqual(host.events[host.events.index('start') - 1], 'fingerprint')
        self.assertLess(host.events.index('verify-open'), host.events.index('resume-health'))
        self.assertEqual(host.events[-1], 'record')
        self.assert_no_repeated_destructive_work(host)

    def test_every_continuation_failure_closes_ingress_workers_and_health(self):
        for failure in ('save:resource-recovery-evidence.json', 'impact',
                        'save:resource-impact-after.json', 'ui', 'verify-candidate:1',
                        'compose', 'fingerprint', 'start', 'verify-candidate:2', 'open', 'verify-open',
                        'resume-health', 'record:1'):
            with self.subTest(failure=failure):
                host = ClosedHost({failure})
                result = recovery().continue_candidate(host, {}, {})
                self.assertFalse(result['published'])
                self.assertTrue(result['maintenance_closed'])
                self.assertTrue(host.closed)
                self.assertFalse(host.backend_running or host.health_running)
                self.assertNotIn('sensitive', str(result))
                self.assertEqual(host.counts['close'], 1)
                self.assert_no_repeated_destructive_work(host)

    def test_failed_close_is_reported_unverified_and_never_reopens(self):
        host = ClosedHost({'verify-open', 'close', 'record'})
        result = recovery().continue_candidate(host, {}, {})
        self.assertFalse(result['published'])
        self.assertFalse(result['maintenance_closed'])
        self.assertEqual(host.counts['open'], 1)
        self.assertEqual(host.events[-2:], ['close', 'record'])
        self.assert_no_repeated_destructive_work(host)

    def test_fresh_start_snapshot_drift_closes_without_starting_workers(self):
        host = ClosedHost()
        host.snapshot = {'changed': True}
        result = recovery().continue_candidate(host, {}, {})
        self.assertFalse(result['published'])
        self.assertTrue(result['maintenance_closed'])
        self.assertEqual(result['failure'], 'backend-start')
        self.assertNotIn('start', host.events)
        self.assertNotIn('open', host.events)
        self.assertIn('close', host.events)
        self.assert_no_repeated_destructive_work(host)


class MemoryRows:
    """Evaluate the actual SSL guard against in-memory job/lease/source rows."""
    def __init__(self, rows): self.rows = list(rows)
    @staticmethod
    def matches(row, conditions):
        for key, expected in conditions.items():
            field, _, operation = key.partition('__')
            actual = getattr(row, field)
            if operation == 'in': matched = actual in expected
            elif operation == 'gt': matched = actual > expected
            else: matched = actual == expected
            if not matched: return False
        return True
    def filter(self, **conditions):
        return MemoryRows(row for row in self.rows if self.matches(row, conditions))
    def exclude(self, **conditions):
        return MemoryRows(row for row in self.rows if not self.matches(row, conditions))
    def exists(self): return bool(self.rows)
    def __iter__(self): return iter(self.rows)


class RecoveryProbeTests(unittest.TestCase):
    def fixtures(self):
        module = recovery()
        stable = module.load('recovery_probe_schema_test', HERE / 'schema.py')
        legacy = (HERE.parent / 'upgrade-review/fingerprint.py').read_text()
        return module, stable, legacy

    def ssl_guard(self):
        module, stable, legacy = self.fixtures()
        probe = module.recovery_probe(stable, legacy)
        function = next(node for node in ast.parse(probe).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'ssl_idle')
        namespace = {'timedelta': timedelta}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<isolated-ssl-guard>', 'exec'), namespace)
        return namespace['ssl_idle']

    def test_probe_changes_only_the_unique_prospective_window_and_keeps_every_other_guard(self):
        module, stable, legacy = self.fixtures()
        expected = stable.build_probe(legacy).replace(
            'horizon = now + timedelta(minutes=15)', 'horizon = now + timedelta(minutes=5)')
        actual = module.recovery_probe(stable, legacy)
        self.assertEqual(actual, expected)
        compile(actual, '<recovery-probe>', 'exec')
        self.assertEqual(legacy.count('horizon = now + timedelta(minutes=15)'), 1)
        for altered in (legacy.replace('horizon = now + timedelta(minutes=15)', 'horizon = now'),
                        legacy + '\nhorizon = now + timedelta(minutes=15)\n'):
            with self.subTest(altered=altered[-60:]), self.assertRaisesRegex(ValueError, 'recovery-idle-window-anchor'):
                module.recovery_probe(stable, altered)

    def test_five_minute_guard_still_rejects_due_sources_active_jobs_and_live_leases(self):
        guard = self.ssl_guard()
        now = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
        def registry(due_minutes=10, jobs=(), leases=(), last_attempt=True):
            source = SimpleNamespace(enabled=True, interval_hours=1,
                last_attempt_at=now - timedelta(hours=1) + timedelta(minutes=due_minutes)
                                if last_attempt else None)
            models = {'SSLJob': MemoryRows(SimpleNamespace(**row) for row in jobs),
                      'SSLLease': MemoryRows(SimpleNamespace(**row) for row in leases),
                      'SSLSource': MemoryRows([source])}
            return SimpleNamespace(get_model=lambda app, name: SimpleNamespace(objects=models[name]))
        self.assertTrue(guard(registry(), now))
        self.assertTrue(guard(registry(due_minutes=5.01), now))
        for due in (-1, 0, 4.99, 5):
            with self.subTest(due=due): self.assertFalse(guard(registry(due_minutes=due), now))
        self.assertFalse(guard(registry(last_attempt=False), now))
        for state in ('queued', 'running', 'prepared', 'uncertain'):
            with self.subTest(state=state):
                self.assertFalse(guard(registry(jobs=[{'state': state, 'verify_requested': False}]), now))
        self.assertFalse(guard(registry(jobs=[{'state': 'completed', 'verify_requested': True}]), now))
        self.assertFalse(guard(registry(leases=[{'expires_at': now + timedelta(seconds=1), 'token': 'active'}]), now))
        self.assertTrue(guard(registry(leases=[{'expires_at': now, 'token': 'active'}]), now))


class SignalClock:
    SIGALRM = 14
    def __init__(self):
        self.previous = object()
        self.handler, self.armed, self.events = self.previous, 0, []
    def signal(self, signum, handler):
        previous, self.handler = self.handler, handler
        self.events.append(('signal', signum, handler))
        return previous
    def alarm(self, seconds):
        self.armed = seconds
        self.events.append(('alarm', seconds))
    def expire(self): self.handler(self.SIGALRM, None)


class RecoveryDeadlineTests(unittest.TestCase):
    def test_deadline_arms_exactly_120_seconds_then_cancels_and_restores_handler(self):
        module, clock = recovery(real_deadline=True), SignalClock()
        with mock.patch.object(module, 'signal', clock):
            with module.deadline():
                self.assertEqual(clock.armed, 120)
                self.assertTrue(callable(clock.handler))
            self.assertEqual(clock.armed, 0)
            self.assertIs(clock.handler, clock.previous)
        self.assertEqual([event for event in clock.events if event[0] == 'alarm'],
                         [('alarm', 120), ('alarm', 0)])

    def test_timeout_at_each_opening_stage_cancels_clock_before_closing(self):
        for at in ('ui', 'start', 'verify-open', 'record'):
            with self.subTest(at=at):
                module, clock, host = recovery(real_deadline=True), SignalClock(), ClosedHost()
                original_action = host.action
                expired = []
                def action(name):
                    if name == 'close': self.assertEqual(clock.armed, 0)
                    original_action(name)
                    if name == at and not expired:
                        expired.append(True)
                        clock.expire()
                host.action = action
                with mock.patch.object(module, 'signal', clock):
                    result = module.continue_candidate(host, {}, {})
                self.assertEqual(expired, [True])
                self.assertFalse(result['published'])
                self.assertTrue(result['maintenance_closed'])
                self.assertTrue(host.closed)
                self.assertFalse(host.backend_running or host.health_running)
                self.assertEqual(clock.armed, 0)
                self.assertIs(clock.handler, clock.previous)
                self.assertEqual(host.counts['close'], 1)


class MainLockTests(unittest.TestCase):
    def run_main(self, module, prepare, lock):
        output = io.StringIO()
        with mock.patch.object(module, 'prepare', side_effect=prepare), \
             mock.patch.object(module, 'load', return_value=SimpleNamespace(publication_lock=lock)), \
             mock.patch.object(module.os, 'umask'), redirect_stdout(output):
            code = module.main(['a' * 64])
        return code, json.loads(output.getvalue())

    def test_preflight_failure_never_claims_closed_or_writes(self):
        module = recovery()
        entered = []
        @contextmanager
        def lock():
            entered.append(True)
            yield
        def failed_prepare(*args): raise ValueError('sensitive preflight details')
        code, result = self.run_main(module, failed_prepare, lock)
        self.assertEqual(code, 1)
        self.assertFalse(result['published'])
        self.assertIsNot(result['maintenance_closed'], True)
        self.assertEqual(entered, [])
        self.assertNotIn('sensitive', str(result))

    def test_locked_revalidation_failure_does_not_write_or_claim_closed(self):
        module = recovery()
        host, held, prepared = ClosedHost(), [False], []
        @contextmanager
        def lock():
            held[0] = True
            try: yield
            finally: held[0] = False
        def prepare(*args):
            prepared.append(held[0])
            if held[0]: raise ValueError('changed between checks')
            return host, {}, {}
        code, result = self.run_main(module, prepare, lock)
        self.assertEqual(code, 1)
        self.assertEqual(prepared, [False, True])
        self.assertEqual(host.events, [])
        self.assertIsNot(result['maintenance_closed'], True)

    def test_all_continuation_writes_follow_locked_revalidation(self):
        module = recovery()
        held, prepared = [False], []
        def guard(): self.assertTrue(held[0]); self.assertEqual(prepared, [False, True])
        host = ClosedHost(guard=guard)
        @contextmanager
        def lock():
            held[0] = True
            try: yield
            finally: held[0] = False
        def prepare(*args):
            prepared.append(held[0])
            return host, {}, {}
        code, result = self.run_main(module, prepare, lock)
        self.assertEqual(code, 0)
        self.assertTrue(result['published'])
        self.assertEqual(prepared, [False, True])
        self.assertFalse(held[0])


class RecoveryBundleTests(unittest.TestCase):
    def test_altered_bundle_is_rejected_before_loading_original_or_stable_code(self):
        module = recovery()
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            manifest = {'profile': module.PROFILE, 'candidate_revision': module.CANDIDATE,
                        'scope_sha256': module.SCOPE_SHA, 'files': {}}
            names = ('recover.py', 'schema.py', 'rehearsal.py', 'contract.py',
                     'production-baseline.json', 'rehearsal.json')
            for name in names:
                (bundle / name).write_bytes(b'approved helper')
                manifest['files'][name] = module.sha(b'approved helper')
            raw = json.dumps(manifest).encode()
            (bundle / 'recovery.json').write_bytes(raw)
            (bundle / 'schema.py').write_bytes(b'altered helper')
            with mock.patch.object(module, 'load') as load, \
                 self.assertRaisesRegex(ValueError, 'recovery-helper-hash'):
                module.prepare(bundle, module.sha(raw))
            load.assert_not_called()


if __name__ == '__main__': unittest.main()
