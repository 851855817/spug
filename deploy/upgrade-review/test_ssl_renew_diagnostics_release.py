"""Restrict SSL diagnostics publication to its adapter and task item UI."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).parent
MODULE = 'apps/baota/operations/certificates.py'
UI = 'spug_web/src/pages/exec/panel/TaskItem.js'
PROFILE = 'ssl-renew-diagnostics'


def load(name):
    spec = importlib.util.spec_from_file_location('ssl_renew_diagnostics_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SSLRenewDiagnosticsReleaseTests(unittest.TestCase):
    def test_scope_requires_exact_certificate_adapter_and_task_item_ui(self):
        package = load('package')
        allowed = ['spug_api/' + MODULE, UI]
        package.validate_runtime_changes(allowed + ['docs/ssl-renew.md',
            'spug_api/apps/baota/test_operations.py', 'spug_web/src/pages/exec/panel/TaskItem.test.js'], PROFILE)
        for names in ([], allowed[:1], allowed[1:], allowed + ['spug_api/apps/baota/inventory/models.py'],
                      allowed + ['spug_web/src/routes.js'], allowed + ['spug_web/package.json']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, PROFILE)

    def test_manifest_and_image_require_only_certificate_overlay_and_both_preserved_modules(self):
        fixtures, package, checker = load('test_package'), load('package'), load('check_image')
        baseline = {package.API_FILE: b'preserved PHP', package.HEALTH_FILE: b'new health'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = fixtures.PackagingTests().profile_stage(root)
            target = stage / 'source/spug_api' / MODULE
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'new certificates')
            release = package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old certificates', PROFILE, baseline)
            self.assertEqual(release['api_sha256'], {MODULE: hashlib.sha256(b'new certificates').hexdigest()})
            self.assertEqual(release['previous_api_sha256'], {MODULE: hashlib.sha256(b'old certificates').hexdigest()})
            runtime = root / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, 'a' * 40)
            for name in baseline:
                path = runtime / 'spug_api' / name
                original = path.read_bytes(); path.write_bytes(b'drift')
                with self.subTest(name=name), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, 'a' * 40)
                path.write_bytes(original)
            for changes in ({'api_sha256': {}}, {'previous_api_sha256': {}},
                            {'preserved_api_sha256': {}}, {'api_sha256': {MODULE: 'c' * 64, package.API_FILE: 'd' * 64}}):
                (stage / 'release.json').write_text(json.dumps(dict(release, **changes)))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, 'a' * 40)
            for preserved in (None, {}, {package.API_FILE: b'preserved PHP'}, dict(baseline, **{package.HEALTH_FILE: b'drift'})):
                with self.subTest(preserved=preserved), self.assertRaises(ValueError):
                    package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old certificates', PROFILE, preserved)

    def test_publisher_loads_the_fixed_overlay_and_rejects_missing_preservation(self):
        fixtures, publisher = load('test_publish'), load('publish')
        fixture = fixtures.FileContractTests(); fixture.setUp()
        try:
            stage = fixture.stage('host-report')
            for name in (fixtures.MODULE, fixtures.HEALTH_MODULE):
                (stage / 'source/spug_api' / name).write_bytes(b'old')
            target = stage / 'source/spug_api' / MODULE
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'new')
            release = json.loads((stage / 'release.json').read_text())
            release.update(profile=PROFILE, api_sha256={MODULE: fixtures.NEW}, previous_api_sha256={MODULE: fixtures.OLD},
                preserved_api_sha256={fixtures.MODULE: fixtures.OLD, fixtures.HEALTH_MODULE: fixtures.OLD})
            (stage / 'api-release.json').write_text(json.dumps({'revision': fixtures.REVISION, 'sha256': release['api_sha256']}))
            fixture.rewrite_stage(stage, release)
            self.assertEqual(publisher.load_stage(stage, fixtures.REVISION, fixtures.PREVIOUS)['api_sha256'], {MODULE: fixtures.NEW})
            release['preserved_api_sha256'] = {}
            fixture.rewrite_stage(stage, release)
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.load_stage(stage, fixtures.REVISION, fixtures.PREVIOUS)
        finally:
            fixture.doCleanups()

    def test_backup_and_failure_recovery_preserve_all_other_api_modules(self):
        fixtures, publisher = load('test_publish'), load('publish')
        for failure in (None, 'backup', 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host = fixtures.FakeHost()
                host.release.update(profile=PROFILE, api_sha256={MODULE: fixtures.NEW}, previous_api_sha256={MODULE: fixtures.OLD},
                    preserved_api_sha256={fixtures.MODULE: fixtures.OLD, fixtures.HEALTH_MODULE: fixtures.OLD})
                for root in host.file_state['api']:
                    root.update({MODULE: fixtures.OLD, fixtures.HEALTH_MODULE: fixtures.OLD})
                host.original_files = copy.deepcopy(host.file_state)
                host.fail = failure
                result = publisher.publish_release(host, fixtures.REVISION, fixtures.PREVIOUS)
                self.assertEqual(result['published'], failure is None, result)
                self.assertIn('paired-backup', host.events)
                if failure is None:
                    self.assertLess(host.events.index('paired-backup'), host.events.index('api-publish'))
                    for actual, original in zip(host.file_state['api'], host.original_files['api']):
                        self.assertEqual(actual, dict(original, **{MODULE: fixtures.NEW}))
                else:
                    self.assertEqual(host.file_state, host.original_files)
                self.assertTrue(host.ingress)
                self.assertTrue(host.health)
