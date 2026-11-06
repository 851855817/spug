"""The workbench patch may touch only its fixed UI files, never live API files."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).parent
PROFILE = 'workbench-ui'
UI = {'spug_web/src/pages/home/index.js', 'spug_web/src/pages/home/Todo.js',
      'spug_web/src/pages/home/index.module.less', 'spug_web/src/pages/server-monitor/index.js',
      'spug_web/src/pages/panel-inventory/index.js', 'spug_web/src/pages/server-expiry/index.js',
      'spug_web/src/layout/Sider.js', 'spug_web/src/layout/layout.module.less', 'spug_web/src/layout/navigationOrder.js'}


def load(name):
    spec = importlib.util.spec_from_file_location('workbench_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class WorkbenchReleaseTests(unittest.TestCase):
    def test_exact_ui_scope_accepts_tests_but_refuses_missing_extra_or_backend_files(self):
        package = load('package')
        package.validate_runtime_changes(sorted(UI) + ['docs/guide.md', 'spug_web/src/layout/Sider.test.js'], PROFILE)
        for names in ([], sorted(UI)[1:], sorted(UI) + ['spug_web/src/routes.js'],
                      sorted(UI) + ['spug_api/apps/baota/inventory/service.py']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, PROFILE)

    def test_manifest_and_image_preserve_both_api_modules_with_no_overlay(self):
        fixtures, package, checker = load('test_package'), load('package'), load('check_image')
        baseline = {package.API_FILE: b'preserved PHP', package.HEALTH_FILE: b'new health'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); stage = fixtures.PackagingTests().profile_stage(root)
            release = package.write_manifests(stage, 'a' * 40, 'b' * 40, None, PROFILE, baseline)
            self.assertEqual(release['api_sha256'], {})
            self.assertEqual(release['previous_api_sha256'], {})
            runtime = root / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, 'a' * 40)
            for name in baseline:
                target = runtime / 'spug_api' / name; original = target.read_bytes(); target.write_bytes(b'drift')
                with self.subTest(name=name), self.assertRaises(ValueError): checker.verify(runtime, stage, 'a' * 40)
                target.write_bytes(original)
            for changes in ({'api_sha256': {package.API_FILE: 'f' * 64}}, {'preserved_api_sha256': {}}):
                (stage / 'release.json').write_text(json.dumps(dict(release, **changes)))
                with self.subTest(changes=changes), self.assertRaises(ValueError): checker.verify(runtime, stage, 'a' * 40)

    def test_loading_stage_requires_complete_image_api_preservation_receipt(self):
        fixtures, publisher = load('test_publish'), load('publish')
        fixture = fixtures.FileContractTests(); fixture.setUp()
        try:
            stage = fixture.operations_ui_stage()
            release = json.loads((stage / 'release.json').read_text()); release['profile'] = PROFILE
            fixture.rewrite_stage(stage, release)
            self.assertEqual(publisher.load_stage(stage, fixtures.REVISION, fixtures.PREVIOUS)['api_sha256'], {})
            host = publisher.DockerHost(fixtures.REVISION, fixture.root)
            host.release = release
            for mode in ('publish', 'restore'):
                with self.assertRaises(publisher.ReleaseFailure): host.helper('api', mode)
            with self.assertRaises(publisher.ReleaseFailure): host.verify_receipt('api', {})
            receipt = stage / 'isolated-checks.json'; content = json.loads(receipt.read_text())
            content.pop('unchanged_api_tree_sha256'); receipt.write_text(json.dumps(content))
            with self.assertRaises(publisher.ReleaseFailure): publisher.load_stage(stage, fixtures.REVISION, fixtures.PREVIOUS)
        finally: fixture.doCleanups()

    def test_success_and_partial_failures_never_apply_api_and_use_new_paired_backups(self):
        fixtures, publisher = load('test_publish'), load('publish')
        for failure in (None, 'backup', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                case = fixtures.ControllerTests(); case.setUp(); case.operations_ui_profile()
                host = case.host; host.release['profile'] = PROFILE; host.fail = failure
                before = copy.deepcopy(host.file_state['api'])
                result = publisher.publish_release(host, fixtures.REVISION, fixtures.PREVIOUS)
                self.assertEqual(result['published'], failure is None, result)
                self.assertEqual(host.file_state['api'], before)
                self.assertNotIn('api-publish', host.events); self.assertNotIn('api-restore', host.events)
                self.assertIn('paired-backup', host.events)
                if failure is None: self.assertLess(host.events.index('paired-backup'), host.events.index('ui-publish'))
                else: self.assertEqual(host.file_state, host.original_files)
                self.assertTrue(host.ingress and host.health)
