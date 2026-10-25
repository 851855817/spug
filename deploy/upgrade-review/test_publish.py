"""Failure-driven publisher tests: local state machine and real file receipts only."""
import copy
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
REVISION = 'a' * 40
PREVIOUS = 'b' * 40
MODULE = 'apps/baota/operations/php_update.py'
OLD = hashlib.sha256(b'old').hexdigest()
NEW = hashlib.sha256(b'new').hexdigest()
SPEC = importlib.util.spec_from_file_location('upgrade_publisher', HERE / 'publish.py')
publisher = None
if (HERE / 'publish.py').is_file():
    publisher = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(publisher)


def files(revision=PREVIOUS):
    ui = {'index.html': OLD, 'asset-manifest.json': OLD, 'ui-release.json': OLD,
          'static/old.js': OLD}
    return {'api': [{MODULE: OLD, 'apps/ops/models.py': OLD} for _ in range(2)],
            'ui': [dict(ui) for _ in range(2)], 'ui_revisions': [revision, revision]}


class FakeHost:
    """A mutable host fixture; failures occur after partial state changes."""
    def __init__(self):
        self.events = []
        self.fail = None
        self.file_state = files()
        self.original_files = copy.deepcopy(self.file_state)
        self.business = {'schema_sha256': OLD, 'migration_rows_sha256': OLD,
                         'credentials_decrypt': True, 'baota.key': OLD,
                         'baota.key.sha256': OLD,
                         'ops_expiry': {'count': 7, 'digest': OLD},
                         'baota_inventory': {'count': 3, 'digest': OLD}}
        self.active = False
        self.processes = {'redis': '11', 'spug-worker': '12', 'spug-monitor': '13',
                          'container': 'original:0', 'configuration': OLD}
        self.compose = 'services:\n  spug:\n    image: spug-baota:' + PREVIOUS[:7] + '\n'
        self.health = True
        self.ingress = True
        self.running = True
        self.receipts = {}
        self.release = {'revision': REVISION, 'previous_revision': PREVIOUS,
                        'image': 'spug-baota:' + REVISION[:7],
                        'api_sha256': {MODULE: NEW}, 'previous_api_sha256': {MODULE: OLD},
                        'ui_release_sha256': NEW,
                        'ui_sha256': {'index.html': NEW, 'asset-manifest.json': NEW,
                                      'static/new.js': NEW}}

    def load_release(self, revision, previous):
        self.events.append('validate-stage')
        return self.release

    def live_revision(self):
        return self.file_state['ui_revisions'][0]

    def compose_text(self):
        return self.compose

    def assert_running(self):
        if not self.running:
            raise RuntimeError('not-running')

    def fingerprint(self):
        self.events.append('idle-fingerprint')
        if self.active:
            raise RuntimeError('active new health_policy task')
        return copy.deepcopy(self.business)

    def identity(self):
        return dict(self.processes)

    def runtime_files(self):
        return copy.deepcopy(self.file_state)

    def setup_backup(self, baseline):
        self.events.append('setup-backup')
        return '/private/backup'

    def stop_health(self):
        self.events.append('stop-health')
        self.health = False

    def close(self):
        self.events.append('close')
        self.ingress = self.running = False
        if self.fail == 'close':
            raise RuntimeError('cannot stop worker')

    def backup(self):
        self.events.append('paired-backup')
        if self.fail == 'backup':
            raise RuntimeError('database password=SENSITIVE must never escape')

    def helper(self, kind, mode):
        self.events.append(kind + '-' + mode)
        if mode == 'publish':
            self.receipts[kind] = copy.deepcopy(self.original_files)
            if kind == 'api':
                self.file_state['api'][0][MODULE] = NEW
                if self.fail == 'api-partial':
                    raise RuntimeError('partial API overlay')
                self.file_state['api'][1][MODULE] = NEW
            else:
                for index, root in enumerate(self.file_state['ui']):
                    root.update(self.release['ui_sha256'])
                    root['ui-release.json'] = NEW
                    self.file_state['ui_revisions'][index] = REVISION
                    if self.fail == 'ui-partial' and index == 0:
                        raise RuntimeError('partial UI overlay')
                if self.fail == 'schema':
                    self.business['schema_sha256'] = NEW
                if self.fail == 'credentials':
                    self.business['baota.key'] = NEW
                if self.fail == 'pid':
                    self.processes['redis'] = '99'
            if self.fail == kind + '-bad-publish-receipt':
                return {'revision': PREVIOUS, 'mode': mode}
        else:
            previous = self.receipts[kind]
            self.file_state[kind] = copy.deepcopy(previous[kind])
            if kind == 'ui':
                self.file_state['ui_revisions'] = previous['ui_revisions'][:]
            if self.fail == kind + '-restore-corrupt':
                if kind == 'api':
                    self.file_state['api'][1][MODULE] = NEW
                else:
                    self.file_state['ui'][1]['static/old.js'] = NEW
            if self.fail == kind + '-restore-exception':
                raise RuntimeError('restore failed after writing one file')
            if self.fail == kind + '-bad-restore-receipt':
                return {'revision': PREVIOUS, 'mode': mode}
        return {'revision': REVISION, 'mode': mode, 'modules': 1,
                'verified_files': len(self.release['ui_sha256']) + 1}

    def verify_receipt(self, kind, baseline):
        self.events.append(kind + '-verify-backup-receipt')
        if kind not in self.receipts or self.fail == kind + '-incomplete-receipt':
            raise RuntimeError('missing or corrupt complete.json')

    def write_compose(self, content):
        self.events.append('write-compose')
        self.compose = content

    def validate_compose(self):
        self.events.append('validate-compose')
        if self.fail == 'original-compose-invalid':
            raise RuntimeError('original YAML is invalid despite its matching image line')
        if self.fail in ('api-restore-corrupt', 'ui-restore-corrupt',
                         'api-restore-exception', 'ui-restore-exception',
                         'api-bad-restore-receipt', 'ui-bad-restore-receipt',
                         'api-incomplete-receipt', 'ui-incomplete-receipt') and REVISION[:7] in self.compose:
            raise RuntimeError('fail after publication to exercise restore')

    def start_backend(self):
        self.events.append('start-backend')
        self.running = True

    def open_ingress(self):
        self.events.append('open-ingress')
        self.ingress = True

    def verify_http(self, revision):
        self.events.append('verify-http-' + revision[:7])
        if self.fail in ('active-after-open', 'business-after-open', 'http') and revision == REVISION:
            if self.fail == 'active-after-open':
                self.active = True
            if self.fail == 'business-after-open':
                self.business['ops_expiry']['count'] += 1
            raise RuntimeError('HTTP validation failed')
        if self.fail == 'final-business-drift' and revision == REVISION:
            self.business['ops_expiry']['digest'] = NEW

    def resume_health(self):
        self.events.append('resume-health')
        self.health = True

    def record(self, name, result):
        self.events.append('record-' + name)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(publisher, 'publisher controller is not implemented')
        self.host = FakeHost()

    def run_release(self, failure=None):
        self.host.fail = failure
        return publisher.publish_release(self.host, REVISION, PREVIOUS)

    def assert_closed(self):
        self.assertFalse(self.host.ingress)
        self.assertFalse(self.host.running)
        self.assertFalse(self.host.health)

    def test_success_preserves_nonempty_business_schema_keys_and_original_processes(self):
        result = self.run_release()
        self.assertTrue(result['published'])
        self.assertTrue(self.host.ingress and self.host.health)
        self.assertEqual(self.host.file_state['api'][0][MODULE], NEW)
        self.assertIn('spug-baota:' + REVISION[:7], self.host.compose)
        events = self.host.events
        self.assertLess(events.index('paired-backup'), events.index('api-publish'))
        self.assertLess(events.index('ui-publish'), events.index('write-compose'))
        self.assertLess(events.index('validate-compose', events.index('write-compose')),
                        events.index('open-ingress'))

    def test_backup_failure_never_overlays_and_restores_prior_service_state(self):
        result = self.run_release('backup')
        self.assertFalse(result['published'])
        self.assertTrue(result['rolled_back'])
        self.assertNotIn('api-publish', self.host.events)
        self.assertEqual(self.host.file_state, self.host.original_files)
        self.assertTrue(self.host.ingress and self.host.health)
        self.assertNotIn('SENSITIVE', json.dumps(result))

    def test_partial_api_publication_is_restored_even_when_helper_raised(self):
        result = self.run_release('api-partial')
        self.assertFalse(result['published'])
        self.assertTrue(result['rolled_back'])
        self.assertIn('api-restore', self.host.events)
        self.assertEqual(self.host.file_state, self.host.original_files)
        self.assertNotIn('ui-publish', self.host.events)

    def test_partial_ui_publication_restores_both_real_helper_contracts(self):
        result = self.run_release('ui-partial')
        self.assertTrue(result['rolled_back'])
        self.assertIn('api-restore', self.host.events)
        self.assertIn('ui-restore', self.host.events)
        self.assertEqual(self.host.file_state, self.host.original_files)
        self.assertIn(PREVIOUS[:7], self.host.compose)

    def test_corrupt_incomplete_or_unproven_restores_never_reopen(self):
        for kind in ('api', 'ui'):
            for failure in ('restore-corrupt', 'restore-exception', 'bad-restore-receipt', 'incomplete-receipt'):
                with self.subTest(kind=kind, failure=failure):
                    self.host = FakeHost()
                    result = self.run_release(kind + '-' + failure)
                    self.assertFalse(result['published'])
                    self.assertFalse(result['rolled_back'])
                    self.assertIn(kind + '-restore', self.host.events)
                    self.assert_closed()

    def test_schema_drift_restores_code_but_never_reopens_or_migrates(self):
        result = self.run_release('schema')
        self.assertFalse(result['rolled_back'])
        self.assertIn('api-restore', self.host.events)
        self.assertEqual(self.host.business['schema_sha256'], NEW)
        self.assert_closed()

    def test_key_digest_drift_blocks_service_resume(self):
        result = self.run_release('credentials')
        self.assertFalse(result['rolled_back'])
        self.assert_closed()

    def test_preserved_redis_pid_change_blocks_service_resume(self):
        result = self.run_release('pid')
        self.assertFalse(result['rolled_back'])
        self.assert_closed()

    def test_rollback_after_open_stops_six_services_and_refuses_active_new_policy_task(self):
        result = self.run_release('active-after-open')
        self.assertFalse(result['rolled_back'])
        self.assert_closed()
        self.assertNotIn('api-restore', self.host.events)
        self.assertNotIn('ui-restore', self.host.events)
        self.assertEqual(self.host.file_state['api'][0][MODULE], NEW)
        reopened = self.host.events.index('open-ingress')
        self.assertIn('close', self.host.events[reopened + 1:])

    def test_rollback_after_open_refuses_any_business_drift_before_downgrade(self):
        result = self.run_release('business-after-open')
        self.assertFalse(result['rolled_back'])
        self.assertNotIn('api-restore', self.host.events)
        self.assert_closed()

    def test_failed_http_with_idle_unchanged_business_can_restore_old_release(self):
        result = self.run_release('http')
        self.assertTrue(result['rolled_back'])
        self.assertEqual(self.host.file_state, self.host.original_files)
        self.assertTrue(self.host.ingress and self.host.health)

    def test_final_business_drift_is_detected_before_success_or_auto_recovery(self):
        result = self.run_release('final-business-drift')
        self.assertFalse(result['published'])
        self.assertFalse(result['rolled_back'])
        self.assertNotIn('api-restore', self.host.events)
        self.assert_closed()

    def test_incomplete_publication_result_is_never_called_success(self):
        for kind in ('api', 'ui'):
            with self.subTest(kind=kind):
                self.host = FakeHost()
                result = self.run_release(kind + '-bad-publish-receipt')
                self.assertFalse(result['published'])
                self.assertTrue(result['rolled_back'])

    def test_wrong_previous_ui_stops_nothing(self):
        self.host.file_state['ui_revisions'] = ['c' * 40] * 2
        result = self.run_release()
        self.assertFalse(result['published'])
        self.assertNotIn('stop-health', self.host.events)
        self.assertNotIn('close', self.host.events)

    def test_wrong_or_duplicate_compose_image_stops_nothing(self):
        for content in ('image: spug-baota:unexpected\n', self.host.compose * 2):
            with self.subTest(content=content):
                self.host = FakeHost()
                self.host.compose = content
                result = self.run_release()
                self.assertFalse(result['published'])
                self.assertNotIn('stop-health', self.host.events)

    def test_invalid_original_compose_refuses_before_backup_stop_or_source_write(self):
        result = self.run_release('original-compose-invalid')
        self.assertFalse(result['published'])
        self.assertEqual(self.host.file_state, self.host.original_files)
        self.assertNotIn('setup-backup', self.host.events)
        self.assertNotIn('stop-health', self.host.events)
        self.assertNotIn('close', self.host.events)
        self.assertNotIn('api-publish', self.host.events)
        self.assertTrue(self.host.ingress and self.host.health and self.host.running)

    def test_previous_runtime_hash_mismatch_stops_nothing(self):
        self.host.file_state['api'][1][MODULE] = NEW
        result = self.run_release()
        self.assertFalse(result['published'])
        self.assertNotIn('stop-health', self.host.events)

    def test_changed_existing_static_asset_refuses_publication_before_maintenance(self):
        self.host.release['ui_sha256']['static/old.js'] = NEW
        result = self.run_release()
        self.assertFalse(result['published'])
        self.assertNotIn('stop-health', self.host.events)

    def test_identical_shared_static_asset_and_new_asset_remain_publishable(self):
        self.host.release['ui_sha256']['static/old.js'] = OLD
        result = self.run_release()
        self.assertTrue(result['published'])

    def test_changed_service_worker_is_allowed_only_as_a_backed_up_entrypoint(self):
        for root in self.host.file_state['ui']:
            root['service-worker.js'] = OLD
        self.host.original_files = copy.deepcopy(self.host.file_state)
        self.host.release['ui_sha256']['service-worker.js'] = NEW
        result = self.run_release('http')
        self.assertTrue(result['rolled_back'])
        self.assertEqual(self.host.file_state, self.host.original_files)

    def test_failure_to_stop_a_maintenance_worker_never_publishes_or_restores(self):
        result = self.run_release('close')
        self.assertFalse(result['published'])
        self.assertNotIn('api-publish', self.host.events)
        self.assertNotIn('start-backend', self.host.events)
        self.assertFalse(self.host.health)


class RealUiHelperTests(unittest.TestCase):
    def setUp(self):
        path = HERE / 'publish-ui.py'
        self.assertTrue(path.is_file(), 'four-entrypoint reversible UI helper is missing')
        spec = importlib.util.spec_from_file_location('four_entrypoint_ui', path)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage = self.root / 'stage/build'
        self.stage.mkdir(parents=True)
        self.roots = [self.root / 'startup/build', self.root / 'live/build']
        self.backup = self.root / 'backups' / REVISION
        self.originals = {}
        for index, root in enumerate(self.roots):
            root.mkdir(parents=True)
            for name in ('index.html', 'asset-manifest.json', 'ui-release.json', 'service-worker.js'):
                content = (str(index) + ':old:' + name).encode()
                (root / name).write_bytes(content)
                self.originals[str(index) + '/' + name] = content
        for name in ('index.html', 'asset-manifest.json', 'service-worker.js', 'static/new.js'):
            path = self.stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('new:' + name).encode())
        hashes = {path.relative_to(self.stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.stage.rglob('*') if path.is_file()}
        (self.stage / 'ui-release.json').write_text(json.dumps({'revision': REVISION, 'sha256': hashes}))

    def call(self, mode):
        return self.helper.publish_ui(REVISION, mode, stage=self.stage,
                                      roots=self.roots, backup=self.backup)

    def assert_originals(self):
        for key, expected in self.originals.items():
            index, name = key.split('/', 1)
            destination = self.roots[int(index)] / name
            if expected is None:
                self.assertFalse(destination.exists(), key)
            else:
                self.assertEqual(destination.read_bytes(), expected, key)

    def test_success_and_explicit_restore_preserve_old_service_worker_in_both_roots(self):
        result = self.call('publish')
        self.assertEqual(result['revision'], REVISION)
        self.assertEqual(result['verified_files'], 5)
        receipt = json.loads((self.backup / 'complete.json').read_text())
        self.assertEqual(set(receipt['originals']), set(self.originals))
        for index, root in enumerate(self.roots):
            self.assertEqual((root / 'service-worker.js').read_bytes(), b'new:service-worker.js')
            self.assertEqual((self.backup / str(index) / 'service-worker.js').read_bytes(),
                             self.originals[str(index) + '/service-worker.js'])
        self.call('restore')
        self.assert_originals()

    def test_failure_after_service_worker_write_restores_original_bytes(self):
        original_replace = self.helper.os.replace
        failed = [False]
        def replace(source, destination):
            if Path(destination) == self.roots[1] / 'service-worker.js' and not failed[0]:
                receipt = json.loads((self.backup / 'complete.json').read_text())
                self.assertEqual(set(receipt['originals']), set(self.originals))
                original_replace(source, destination)
                failed[0] = True
                raise OSError('injected failure after service worker write')
            return original_replace(source, destination)
        with mock.patch.object(self.helper.os, 'replace', side_effect=replace):
            with self.assertRaises(Exception):
                self.call('publish')
        self.assertTrue(failed[0])
        self.assert_originals()

    def test_initially_absent_service_worker_is_removed_on_explicit_restore(self):
        for index, root in enumerate(self.roots):
            (root / 'service-worker.js').unlink()
            self.originals[str(index) + '/service-worker.js'] = None
        self.call('publish')
        receipt = json.loads((self.backup / 'complete.json').read_text())
        self.assertIsNone(receipt['originals']['0/service-worker.js'])
        self.assertIsNone(receipt['originals']['1/service-worker.js'])
        self.call('restore')
        self.assert_originals()

    def test_initially_absent_service_worker_is_removed_after_partial_publication(self):
        for index, root in enumerate(self.roots):
            (root / 'service-worker.js').unlink()
            self.originals[str(index) + '/service-worker.js'] = None
        original_replace = self.helper.os.replace
        failed = [False]
        def replace(source, destination):
            if Path(destination) == self.roots[1] / 'service-worker.js' and not failed[0]:
                original_replace(source, destination)
                failed[0] = True
                raise OSError('failure after creating previously absent service worker')
            return original_replace(source, destination)
        with mock.patch.object(self.helper.os, 'replace', side_effect=replace):
            with self.assertRaises(Exception):
                self.call('publish')
        self.assertTrue(failed[0])
        self.assert_originals()

    def test_missing_or_corrupt_fourth_receipt_refuses_before_restoring_any_entry(self):
        self.call('publish')
        complete = self.backup / 'complete.json'
        original_receipt = complete.read_bytes()
        saved = self.backup / '1/service-worker.js'
        original_saved = saved.read_bytes()
        for mode in ('missing-entry', 'corrupt-saved', 'missing-saved'):
            with self.subTest(mode=mode):
                complete.write_bytes(original_receipt)
                saved.write_bytes(original_saved)
                if mode == 'missing-entry':
                    receipt = json.loads(complete.read_text())
                    receipt['originals'].pop('1/service-worker.js')
                    complete.write_text(json.dumps(receipt))
                elif mode == 'corrupt-saved':
                    saved.write_bytes(b'bad backup')
                else:
                    saved.unlink()
                before = {str(index) + '/' + name: (root / name).read_bytes()
                          for index, root in enumerate(self.roots)
                          for name in ('index.html', 'asset-manifest.json', 'ui-release.json', 'service-worker.js')}
                with self.assertRaises(Exception):
                    self.call('restore')
                after = {str(index) + '/' + name: (root / name).read_bytes()
                         for index, root in enumerate(self.roots)
                         for name in ('index.html', 'asset-manifest.json', 'ui-release.json', 'service-worker.js')}
                self.assertEqual(after, before)


class FileContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(publisher, 'publisher file verification is not implemented')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def probe(self, request):
        completed = subprocess.run([sys.executable, '-c', publisher.FILE_PROBE, json.dumps(request)],
                                   capture_output=True, text=True)
        return completed

    def test_real_backup_receipt_requires_every_original_and_correct_saved_hash(self):
        backup = self.root / 'backup'
        backup.mkdir()
        saved = backup / '0' / MODULE
        saved.parent.mkdir(parents=True)
        saved.write_bytes(b'old')
        expected = {'0/' + MODULE: OLD, '1/' + MODULE: None}
        receipt = {'revision': REVISION, 'originals': dict(expected)}
        (backup / 'complete.json').write_text(json.dumps(receipt))
        request = {'mode': 'receipt', 'backup': str(backup), 'revision': REVISION, 'expected': expected}
        self.assertEqual(self.probe(request).returncode, 0)
        saved.write_bytes(b'corrupt')
        self.assertNotEqual(self.probe(request).returncode, 0)
        saved.write_bytes(b'old')
        receipt['originals'].pop('1/' + MODULE)
        (backup / 'complete.json').write_text(json.dumps(receipt))
        self.assertNotEqual(self.probe(request).returncode, 0)

    def test_restored_runtime_rejects_changed_untouched_api_or_old_static_asset(self):
        baseline = files()
        for kind, name in (('api', 'apps/ops/models.py'), ('ui', 'static/old.js')):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(baseline)
                changed[kind][1][name] = NEW
                with self.assertRaises(publisher.ReleaseFailure):
                    publisher.verify_restored_files(changed, baseline, {'static/new.js': NEW})

    def test_restore_allows_only_expected_new_hashed_assets(self):
        baseline = files()
        changed = copy.deepcopy(baseline)
        changed['ui'][0]['static/new.js'] = NEW
        publisher.verify_restored_files(changed, baseline, {'static/new.js': NEW})
        changed['ui'][0]['unexpected.js'] = NEW
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.verify_restored_files(changed, baseline, {'static/new.js': NEW})

    def test_real_file_probe_scans_both_roots_and_rejects_symlink_or_missing_root(self):
        roots = {}
        for kind in ('api', 'ui'):
            roots[kind] = []
            for index in range(2):
                directory = self.root / (kind + str(index))
                directory.mkdir()
                (directory / 'plain.py').write_bytes(b'old')
                if kind == 'ui':
                    (directory / 'ui-release.json').write_text(json.dumps({'revision': PREVIOUS}))
                roots[kind].append(str(directory))
        request = {'mode': 'snapshot', 'roots': roots}
        result = self.probe(request)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['api'][1]['plain.py'], OLD)
        request['roots']['api'][1] += '-missing'
        self.assertNotEqual(self.probe(request).returncode, 0)

    def test_api_top_level_log_append_is_excluded_but_code_config_and_data_are_frozen(self):
        roots = {}
        for kind in ('api', 'ui'):
            roots[kind] = []
            for index in range(2):
                directory = self.root / (kind + str(index))
                directory.mkdir()
                for name in ('runtime.py', 'spug/overrides.py', 'data/key.json',
                             'apps/ops/logs/runtime.py', 'logs/worker.log'):
                    path = directory / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'old')
                if kind == 'ui':
                    (directory / 'ui-release.json').write_text(json.dumps({'revision': PREVIOUS}))
                roots[kind].append(str(directory))
        request = {'mode': 'snapshot', 'roots': roots}
        before = json.loads(self.probe(request).stdout)
        (Path(roots['api'][1]) / 'logs/worker.log').write_bytes(b'log appended on worker restart')
        after = json.loads(self.probe(request).stdout)
        self.assertEqual(before, after, 'API service log writes must not mimic source drift')
        self.assertIn('logs/worker.log', before['ui'][0])
        for name in ('runtime.py', 'spug/overrides.py', 'data/key.json', 'apps/ops/logs/runtime.py'):
            with self.subTest(name=name):
                path = Path(roots['api'][0]) / name
                path.write_bytes(b'new')
                self.assertNotEqual(before, json.loads(self.probe(request).stdout))
                path.write_bytes(b'old')

    def test_exact_root_api_supervisor_log_is_volatile_but_nearby_names_are_frozen(self):
        roots = {}
        for kind in ('api', 'ui'):
            roots[kind] = []
            for index in range(2):
                root = self.root / (kind + str(index))
                root.mkdir()
                for name in ('runtime.py', 'supervisord.log', 'supervisord.log.py',
                             'supervisord.log.1', 'apps/ops/supervisord.log'):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'old')
                if kind == 'ui':
                    (root / 'ui-release.json').write_text(json.dumps({'revision': PREVIOUS}))
                roots[kind].append(str(root))
        request = {'mode': 'snapshot', 'roots': roots}
        before = json.loads(self.probe(request).stdout)
        log = Path(roots['api'][1]) / 'supervisord.log'
        log.write_bytes(b'supervisor stop/start appended')
        self.assertEqual(before, json.loads(self.probe(request).stdout))
        log.unlink()
        self.assertEqual(before, json.loads(self.probe(request).stdout))
        log.write_bytes(b'new supervisor log')
        self.assertEqual(before, json.loads(self.probe(request).stdout))
        for kind, name in (('api', 'runtime.py'), ('api', 'supervisord.log.py'),
                           ('api', 'supervisord.log.1'), ('api', 'apps/ops/supervisord.log'),
                           ('ui', 'supervisord.log')):
            with self.subTest(kind=kind, name=name):
                path = Path(roots[kind][0]) / name
                path.write_bytes(b'changed protected file')
                self.assertNotEqual(before, json.loads(self.probe(request).stdout))
                path.write_bytes(b'old')

    def test_main_refuses_lock_contention_before_constructing_a_host_or_publishing(self):
        with mock.patch.object(publisher, 'publication_lock', create=True,
                               side_effect=publisher.ReleaseFailure('publisher-already-running')), \
                mock.patch.object(publisher, 'DockerHost') as host, \
                mock.patch.object(publisher, 'publish_release', return_value={'published': True}) as publish, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = publisher.main([REVISION, PREVIOUS])
        self.assertEqual(result, 1)
        self.assertFalse(host.called)
        self.assertFalse(publish.called)
        self.assertEqual(json.loads(output.getvalue())['failure'], 'publisher-lock-unavailable')

    def test_lock_is_exclusive_nonblocking_private_and_released_after_context_exit(self):
        self.assertTrue(hasattr(publisher, 'publication_lock'), 'publisher process lock is missing')
        held = set()
        calls = []
        def flock(descriptor, operation):
            calls.append(operation)
            if operation == 8:
                held.discard('publisher')
            elif 'publisher' in held:
                raise BlockingIOError('another publisher holds the lock')
            else:
                held.add('publisher')
        fake = types.SimpleNamespace(flock=flock, LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)
        path = self.root / 'publisher.lock'
        with mock.patch.dict(sys.modules, {'fcntl': fake}):
            with publisher.publication_lock(path):
                self.assertTrue(path.exists())
                with self.assertRaises(publisher.ReleaseFailure):
                    with publisher.publication_lock(path):
                        self.fail('second publisher entered')
            with publisher.publication_lock(path):
                self.assertIn('publisher', held)
        self.assertEqual(held, set())
        self.assertEqual(calls, [6, 6, 8, 6, 8])

    @unittest.skipUnless(sys.platform == 'linux', 'real flock is a Linux deployment guard')
    def test_real_linux_flock_rejects_second_process_and_releases_on_exit(self):
        path = self.root / 'publisher.lock'
        child = ("import importlib.util,sys; from pathlib import Path; "
                 "spec=importlib.util.spec_from_file_location('publisher',sys.argv[1]); "
                 "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                 "guard=module.publication_lock(Path(sys.argv[2])); guard.__enter__(); guard.__exit__(None,None,None)")
        def contender():
            return subprocess.run([sys.executable, '-c', child, str(HERE / 'publish.py'), str(path)],
                                  capture_output=True, text=True, timeout=5)
        with publisher.publication_lock(path):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(contender().returncode, 0)
        self.assertEqual(contender().returncode, 0)

    def test_compose_replacement_cannot_match_similar_or_commented_image(self):
        for text in ('# image: spug-baota:' + PREVIOUS[:7] + '\n',
                     'image: spug-baota:' + PREVIOUS[:7] + '-extra\n'):
            with self.subTest(text=text), self.assertRaises(publisher.ReleaseFailure):
                publisher.next_compose(text, PREVIOUS, REVISION)

    def stage(self):
        stage = self.root / ('upgrade-' + REVISION[:7])
        (stage / 'build').mkdir(parents=True)
        api = stage / 'source/spug_api' / MODULE
        api.parent.mkdir(parents=True)
        api.write_bytes(b'new')
        for name in ('index.html', 'asset-manifest.json'):
            (stage / 'build' / name).write_bytes(b'new')
        for name in publisher.HELPERS:
            (stage / name).write_bytes(b'# reviewed helper')
        release = {'revision': REVISION, 'previous_revision': PREVIOUS,
                   'image': 'spug-baota:' + REVISION[:7], 'schema_changes': False,
                   'api_sha256': {MODULE: NEW}, 'previous_api_sha256': {MODULE: OLD},
                   'helper_sha256': {name: publisher.digest(stage / name) for name in publisher.HELPERS}}
        ui = {'revision': REVISION, 'sha256': {'index.html': NEW, 'asset-manifest.json': NEW}}
        (stage / 'release.json').write_text(json.dumps(release))
        (stage / 'api-release.json').write_text(json.dumps({'revision': REVISION, 'sha256': release['api_sha256']}))
        (stage / 'build/ui-release.json').write_text(json.dumps(ui))
        archive = stage.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as output:
            output.add(stage, arcname=stage.name)
        checks = {'revision': REVISION, 'previous_revision': PREVIOUS,
                  'image': release['image'], 'schema_changes': False,
                  'linux_regression': True, 'release_failure_tests': True,
                  'image_wiring': True, 'archive_sha256': publisher.digest(archive),
                  'image_id': 'sha256:' + NEW}
        (stage / 'isolated-checks.json').write_text(json.dumps(checks))
        return stage

    def test_stage_requires_all_prepare_receipts_and_unchanged_archive(self):
        stage = self.stage()
        publisher.load_stage(stage, REVISION, PREVIOUS)
        path = stage / 'isolated-checks.json'
        original = json.loads(path.read_text())
        for name in ('schema_changes', 'linux_regression', 'release_failure_tests',
                     'image_wiring', 'previous_revision', 'image', 'archive_sha256', 'image_id'):
            for mode in ('missing', 'wrong'):
                with self.subTest(name=name, mode=mode):
                    checks = dict(original)
                    if mode == 'missing':
                        checks.pop(name)
                    else:
                        checks[name] = 'wrong'
                    path.write_text(json.dumps(checks))
                    with self.assertRaises((publisher.ReleaseFailure, KeyError)):
                        publisher.load_stage(stage, REVISION, PREVIOUS)
        path.write_text(json.dumps(original))
        stage.with_suffix('.tar.gz').write_bytes(b'changed')
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_stage_rejects_additional_api_file_tampered_helper_or_asset(self):
        stage = self.stage()
        for name in ('apply-expiry.py', 'build/index.html', 'source/spug_api/' + MODULE):
            with self.subTest(name=name):
                path = stage / name
                previous = path.read_bytes()
                path.write_bytes(b'tampered')
                with self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)
                path.write_bytes(previous)
        release = json.loads((stage / 'release.json').read_text())
        release['api_sha256']['apps/ops/models.py'] = NEW
        (stage / 'release.json').write_text(json.dumps(release))
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_unlisted_ui_asset_cannot_enter_a_future_recreate_image(self):
        stage = self.stage()
        (stage / 'build/unlisted.js').write_bytes(b'unreviewed')
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_jointly_changed_runtime_and_manifests_cannot_evade_the_prepared_archive(self):
        stage = self.stage()
        content = b'unreviewed jointly edited runtime'
        (stage / 'source/spug_api' / MODULE).write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        release_path = stage / 'release.json'
        release = json.loads(release_path.read_text())
        release['api_sha256'][MODULE] = expected
        release_path.write_text(json.dumps(release))
        api_path = stage / 'api-release.json'
        api = json.loads(api_path.read_text())
        api['sha256'][MODULE] = expected
        api_path.write_text(json.dumps(api))
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_unarchived_source_file_is_rejected_even_with_valid_runtime_manifests(self):
        stage = self.stage()
        (stage / 'source/spug_api/unarchived.py').write_bytes(b'unreviewed')
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_archive_binding_rejects_unsafe_linked_or_duplicate_members(self):
        stage = self.stage()
        archive = stage.with_suffix('.tar.gz')
        for name, kind in (('../outside', 'file'), (stage.name + '/link', 'link'),
                           (stage.name + '/duplicate', 'duplicate')):
            with self.subTest(name=name):
                with tarfile.open(archive, 'w:gz') as output:
                    member = tarfile.TarInfo(name)
                    if kind == 'link':
                        member.type = tarfile.SYMTYPE
                        member.linkname = '/etc'
                        output.addfile(member)
                    else:
                        member.size = 3
                        output.addfile(member, io.BytesIO(b'new'))
                        if kind == 'duplicate':
                            output.addfile(member, io.BytesIO(b'new'))
                checks_path = stage / 'isolated-checks.json'
                checks = json.loads(checks_path.read_text())
                checks['archive_sha256'] = publisher.digest(archive)
                checks_path.write_text(json.dumps(checks))
                with self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_real_transfer_probe_rejects_partial_or_corrupted_copies(self):
        destination = self.root / 'destination'
        destination.mkdir()
        (destination / 'helper.py').write_bytes(b'new')
        request = {'mode': 'transfer', 'destination': str(destination),
                   'expected': {'helper.py': NEW}}
        self.assertEqual(self.probe(request).returncode, 0)
        (destination / 'helper.py').write_bytes(b'corrupt')
        self.assertNotEqual(self.probe(request).returncode, 0)
        (destination / 'helper.py').unlink()
        self.assertNotEqual(self.probe(request).returncode, 0)

    def test_emitted_backup_manifest_is_accepted_by_existing_health_diagnostic(self):
        self.assertTrue(hasattr(publisher, 'write_backup_manifests'),
                        'publisher needs a health-compatible paired checksum writer')
        backup = self.root / 'backups/20261025-220000-before-upgrade'
        backup.mkdir(parents=True)
        for name in ('database.sql.gz', 'spug-data.tar.gz', 'docker-compose.yml',
                     'spug-health.py', 'health-state.json', 'preservation-before.json'):
            (backup / name).write_bytes(('private fixture ' + name).encode())
        publisher.write_backup_manifests(backup)
        health_path = HERE.parent / 'platform-health/spug-health.py'
        spec = importlib.util.spec_from_file_location('release_health_contract', health_path)
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)
        diagnostic = health.HostDiagnostic(root=self.root,
                                           runner=lambda *_: self.fail('backup check must be local'))
        self.assertEqual(diagnostic.backup_check(verify=True)['status'], 'ok')
        names = {line.split('  ')[1] for line in (backup / 'SHA256SUMS').read_text().splitlines()}
        self.assertEqual(names, {'database.sql.gz', 'spug-data.tar.gz'})
        receipt = json.loads((backup / 'backup-content.json').read_text())
        self.assertEqual(set(receipt['sha256']), {'docker-compose.yml', 'spug-health.py',
                                                'health-state.json', 'preservation-before.json'})
        publisher.verify_backup_manifests(backup)
        for name in ('database.sql.gz', 'docker-compose.yml'):
            with self.subTest(name=name):
                path = backup / name
                original = path.read_bytes()
                path.write_bytes(b'corrupted backup')
                with self.assertRaises(publisher.ReleaseFailure):
                    publisher.verify_backup_manifests(backup)
                path.write_bytes(original)

    def test_backup_stamp_uses_server_local_clock_so_health_selects_the_new_pair(self):
        previous = self.root / 'backups/20261025-152547-before-expiry'
        previous.mkdir(parents=True)
        for name in ('database.sql.gz', 'spug-data.tar.gz', 'docker-compose.yml'):
            (previous / name).write_bytes(b'old fixture')
        publisher.write_backup_manifests(previous)
        # Make choosing this old pair observable through the real diagnostic.
        (previous / 'database.sql.gz').write_bytes(b'old pair now fails verification')
        host = publisher.DockerHost(REVISION, self.root)
        (self.root / 'docker-compose.yml').write_text('compose fixture')
        (self.root / 'platform-health').mkdir()
        (self.root / 'platform-health/spug-health.py').write_text('health fixture')
        def run(command, **kwargs):
            if command[:2] == ['docker', 'cp']:
                Path(command[-1]).write_text('supervisor fixture')
            if command[:2] == ['systemctl', 'is-active']:
                return 'active' if command[-1].endswith('.timer') else 'inactive'
            return 'fixed health configuration'
        host.run = run
        def now(tz=None):
            return datetime(2026, 10, 25, 22, 26, 2) if tz is None else datetime(2026, 10, 25, 14, 26, 2, tzinfo=timezone.utc)
        with mock.patch.object(publisher, 'datetime', types.SimpleNamespace(now=now)):
            host.setup_backup({})
        for name in ('database.sql.gz', 'spug-data.tar.gz'):
            (host.backup_directory / name).write_bytes(b'new verified archive fixture')
        publisher.write_backup_manifests(host.backup_directory)
        spec = importlib.util.spec_from_file_location('latest_backup_health',
                                                      HERE.parent / 'platform-health/spug-health.py')
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)
        self.assertEqual(health.HostDiagnostic(root=self.root).backup_check(verify=True)['status'], 'ok')
        self.assertTrue(host.backup_directory.name.startswith('20261025-222602-'))

    def test_checksum_writer_verifies_bytes_again_after_writing_receipts(self):
        self.assertTrue(hasattr(publisher, 'write_backup_manifests'),
                        'publisher needs post-write backup verification')
        backup = self.root / 'backup'
        backup.mkdir()
        for name in ('database.sql.gz', 'spug-data.tar.gz', 'docker-compose.yml'):
            (backup / name).write_bytes(b'original backup fixture')
        original_open = Path.open
        class CorruptAfterClose:
            def __init__(self, path, *args, **kwargs):
                self.stream = original_open(path, *args, **kwargs)
            def __enter__(self):
                return self.stream.__enter__()
            def __exit__(self, *args):
                result = self.stream.__exit__(*args)
                (backup / 'docker-compose.yml').write_bytes(b'changed during checksum write')
                return result
        def opening(path, *args, **kwargs):
            if path.name == 'backup-content.json' and args and args[0] == 'x':
                return CorruptAfterClose(path, *args, **kwargs)
            return original_open(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', opening):
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.write_backup_manifests(backup)

    def test_real_docker_adapter_stops_exactly_six_and_proves_every_process_stopped(self):
        host = publisher.DockerHost(REVISION, self.root)
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            action = command[4]
            if action == 'status':
                return '\n'.join(name + ' ' + ('RUNNING' if name in publisher.PRESERVED else 'STOPPED')
                                  for name in publisher.MAINTENANCE + publisher.PRESERVED)
            return '0'
        host.run = run
        host.close()
        self.assertEqual(commands[0][4:], ['stop', *publisher.MAINTENANCE])
        self.assertEqual([command[5] for command in commands if command[4] == 'pid'],
                         list(publisher.MAINTENANCE))

    def test_supervisor_status_rc3_with_confirmed_stopped_services_is_valid_maintenance(self):
        host = publisher.DockerHost(REVISION, self.root)
        commands = []
        states = '\n'.join(name + ' ' + ('RUNNING' if name in publisher.PRESERVED else 'STOPPED')
                           for name in publisher.MAINTENANCE + publisher.PRESERVED)
        def child(command, **kwargs):
            commands.append(command)
            if command[4] == 'status':
                return subprocess.CompletedProcess(command, 3, stdout=states)
            return subprocess.CompletedProcess(command, 0, stdout='0')
        with mock.patch.object(publisher.subprocess, 'run', side_effect=child):
            try:
                host.close()
            except publisher.ReleaseFailure:
                self.fail('Supervisor rc3 must be parsed when the nine services prove safe maintenance')
        self.assertEqual(len([command for command in commands if command[4] == 'pid']), 6)
        for bad in ('', states.replace('nginx STOPPED', 'nginx UNKNOWN'),
                    states.replace('spug-monitor RUNNING', 'unexpected RUNNING')):
            with self.subTest(status=bad):
                def invalid(command, **kwargs):
                    return subprocess.CompletedProcess(command, 3 if command[4] == 'status' else 0,
                                                       stdout=bad if command[4] == 'status' else '0')
                with mock.patch.object(publisher.subprocess, 'run', side_effect=invalid):
                    with self.assertRaises(publisher.ReleaseFailure):
                        host.close()

    def test_child_process_error_cannot_echo_database_credentials(self):
        host = publisher.DockerHost(REVISION, self.root)
        with mock.patch.object(publisher.subprocess, 'run',
                               side_effect=RuntimeError('password=TOP-SECRET')):
            with self.assertRaises(publisher.ReleaseFailure) as caught:
                host.run(['test'])
        self.assertNotIn('TOP-SECRET', str(caught.exception))

    def test_real_adapter_refuses_low_memory_disk_or_wrong_image_before_mutation(self):
        stage = self.stage()
        host = publisher.DockerHost(REVISION, self.root)
        commands = []
        label = [REVISION]
        image_id = ['sha256:' + NEW]
        def run(command, **kwargs):
            commands.append(command)
            if '{{.Id}}' in command:
                return image_id[0]
            return label[0]
        host.run = run
        with mock.patch.object(publisher, 'available_memory', return_value=512 * 1024**2, create=True), \
                mock.patch.object(publisher.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 3 * 1024**3})()):
            host.load_release(REVISION, PREVIOUS)
            self.assertTrue(commands, 'publisher must independently inspect the prepared image')
            self.assertEqual(commands[-1][-1], 'spug-baota:' + REVISION[:7])
            self.assertTrue(any('{{.Id}}' in command for command in commands),
                            'publisher must bind the exact prepared image, not only its label')
            image_id[0] = 'sha256:' + OLD
            with self.assertRaises(publisher.ReleaseFailure):
                host.load_release(REVISION, PREVIOUS)
            image_id[0] = 'sha256:' + NEW
            label[0] = PREVIOUS
            with self.assertRaises(publisher.ReleaseFailure):
                host.load_release(REVISION, PREVIOUS)
            label[0] = REVISION
            with mock.patch.object(publisher, 'available_memory', return_value=255 * 1024**2):
                with self.assertRaises(publisher.ReleaseFailure):
                    host.load_release(REVISION, PREVIOUS)
            with mock.patch.object(publisher.shutil, 'disk_usage',
                                   return_value=type('Usage', (), {'free': 1024**3})()):
                with self.assertRaises(publisher.ReleaseFailure):
                    host.load_release(REVISION, PREVIOUS)


if __name__ == '__main__':
    unittest.main()
