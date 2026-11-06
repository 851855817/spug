"""Three existing ops modules, two UI files, no model or schema overlays."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).parent
PROFILE = 'continuous-inspections'
MODULES = ('apps/ops/service.py', 'apps/ops/worker.py', 'apps/ops/runner.py')
UI = {'spug_web/src/pages/exec/operations/RunForm.js', 'spug_web/src/pages/exec/operations/RunDetail.js'}
PHP, HEALTH = 'apps/baota/operations/php_update.py', 'apps/setting/health.py'
REVISION, PREVIOUS = 'a' * 40, 'b' * 40


def load(name):
    spec = importlib.util.spec_from_file_location('continuous_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ContinuousReleaseTests(unittest.TestCase):
    def stage(self, root):
        stage = load('test_package').PackagingTests().profile_stage(Path(root))
        for name in MODULES:
            target = stage / 'source/spug_api' / name
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'new ' + name.encode())
        return stage

    def manifest(self, stage):
        return load('package').write_manifests(stage, REVISION, PREVIOUS,
            {name: b'old ' + name.encode() for name in MODULES}, PROFILE,
            {PHP: b'preserved PHP', HEALTH: b'new health'})

    def test_exact_runtime_scope_rejects_models_extra_api_and_dependency_changes(self):
        package = load('package'); names = ['spug_api/' + n for n in MODULES] + sorted(UI)
        package.validate_runtime_changes(names + ['spug_api/apps/ops/test_continuous.py'], PROFILE)
        for bad in (names[:-1], names[1:], names + ['spug_api/apps/ops/models.py'],
                    names + ['spug_api/apps/monitor/ssl/jobs.py'], names + ['spug_web/src/routes.js'],
                    names + ['spug_web/package.json'], names + ['spug_api/apps/account/test_extra.py']):
            with self.subTest(bad=bad), self.assertRaises(ValueError): package.validate_runtime_changes(bad, PROFILE)

    def test_existing_hashes_image_overlay_and_prestate_are_strict(self):
        package, checker = load('package'), load('check_image')
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage(root); manifest = self.manifest(stage)
            self.assertEqual(set(manifest['api_sha256']), set(MODULES))
            self.assertTrue(all(manifest['previous_api_sha256'].values()))
            for invalid in (None, {}, {n: None for n in MODULES}):
                with self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, invalid, PROFILE,
                                           {PHP: b'preserved PHP', HEALTH: b'new health'})
            runtime = Path(root) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, REVISION)
            tampered = copy.deepcopy(manifest); tampered['previous_api_sha256'][MODULES[0]] = None
            (stage / 'release.json').write_text(json.dumps(tampered))
            with self.assertRaises(ValueError): checker.verify(runtime, stage, REVISION)

    def test_full_tree_rejects_unrelated_overlay_or_missing_old_module(self):
        prepare = load('prepare')
        old = {n: hashlib.sha256(('old ' + n).encode()).hexdigest() for n in MODULES}
        new = {n: hashlib.sha256(('new ' + n).encode()).hexdigest() for n in MODULES}
        baseline = dict(old, **{PHP: 'c' * 64, 'apps/monitor/ssl/jobs.py': 'd' * 64})
        candidate = dict(baseline, **new)
        self.assertTrue(prepare.continuous_inspections_api_digest(baseline, candidate, old, new))
        for modified in (dict(candidate, **{PHP: 'a' * 64}), {n: h for n, h in candidate.items() if n != PHP}):
            with self.assertRaises(ValueError): prepare.continuous_inspections_api_digest(baseline, modified, old, new)
        with self.assertRaises(ValueError):
            prepare.continuous_inspections_api_digest(baseline, candidate, dict(old, **{MODULES[0]: None}), new)

    def test_publisher_requires_full_tree_receipt_and_all_existing_module_hashes(self):
        publisher = load('publish'); receipts = load('test_appearance_release').AppearanceReleaseTests()
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage(root); manifest = self.manifest(stage)
            receipts.receipt(stage, manifest)
            self.assertEqual(set(publisher.load_stage(stage, REVISION, PREVIOUS)['api_sha256']), set(MODULES))
            for change in ({'baseline_image_id': None}, {'unchanged_api_tree_sha256': None}):
                receipts.receipt(stage, manifest, **change)
                with self.assertRaises(publisher.ReleaseFailure): publisher.load_stage(stage, REVISION, PREVIOUS)
            for name in MODULES:
                invalid = copy.deepcopy(manifest); invalid['previous_api_sha256'][name] = None
                receipts.receipt(stage, invalid)
                with self.assertRaises(publisher.ReleaseFailure): publisher.load_stage(stage, REVISION, PREVIOUS)

    def host(self):
        fixture = load('test_publish'); host = fixture.FakeHost()
        for api in host.file_state['api']:
            api.update({name: fixture.OLD for name in MODULES}); api[HEALTH] = fixture.OLD
        host.original_files = copy.deepcopy(host.file_state)
        host.release.update(profile=PROFILE, api_sha256={name: fixture.NEW for name in MODULES},
            previous_api_sha256={name: fixture.OLD for name in MODULES},
            preserved_api_sha256={PHP: fixture.OLD, HEALTH: fixture.OLD})
        original_helper = host.helper
        def helper(kind, mode):
            receipt = original_helper(kind, mode)
            if kind == 'api': receipt['modules'] = len(MODULES)
            return receipt
        host.helper = helper
        host.business['monitor_ssl_certificate'] = {'count': 11, 'digest': fixture.OLD}
        return host, fixture

    def test_success_and_partial_failure_preserve_business_and_restore_three_modules(self):
        publisher = load('publish')
        for failure in (None, 'backup', 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host, fixture = self.host(); host.fail = failure; before = copy.deepcopy(host.business)
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertEqual(host.business, before)
                self.assertEqual(result['published'], failure is None, result)
                if failure is None:
                    self.assertLess(host.events.index('paired-backup'), host.events.index('api-publish'))
                    self.assertIn('api-verify-backup-receipt', host.events)
                else:
                    self.assertTrue(result['rolled_back'], result)
                    self.assertEqual(host.file_state, host.original_files)

    def test_any_missing_or_drifted_ops_module_stops_before_maintenance(self):
        publisher = load('publish')
        for name in MODULES:
            for index in range(2):
                for invalid in (None, 'f' * 64):
                    host, fixture = self.host()
                    if invalid is None: del host.file_state['api'][index][name]
                    else: host.file_state['api'][index][name] = invalid
                    result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                    self.assertFalse(result['published']); self.assertNotIn('close', host.events)
                    self.assertEqual(result['preflight_check'], 'runtime-baseline')
