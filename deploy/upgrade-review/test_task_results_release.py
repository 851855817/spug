"""Keep the results simplification strictly UI-only, including failure recovery."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location('results_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TaskResultsReleaseTests(unittest.TestCase):
    def test_scope_requires_exactly_the_eight_reviewed_views_and_no_api(self):
        package = load('package')
        allowed = {
            'spug_web/src/pages/exec/operations/RunDetail.js',
            'spug_web/src/pages/exec/panel/TaskDetail.js',
            'spug_web/src/pages/exec/panel/TaskItem.js',
            'spug_web/src/pages/exec/panel/UploadResults.js',
            'spug_web/src/pages/exec/task/Output.js',
            'spug_web/src/pages/exec/transfer/Output.js',
            'spug_web/src/pages/schedule/Info.js',
            'spug_web/src/pages/pipeline/console/Body.js',
        }
        package.validate_runtime_changes(list(allowed) + ['docs/result.md'], 'task-results-ui')
        for names in ([], list(allowed)[1:], list(allowed) + ['spug_api/apps/baota/login_views.py'],
                      list(allowed) + ['spug_web/src/routes.js']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, 'task-results-ui')

    def test_manifests_and_image_have_no_api_overlay_and_require_preserved_modules(self):
        fixtures, package, checker = load('test_package'), load('package'), load('check_image')
        baseline = {package.API_FILE: b'preserved PHP', package.HEALTH_FILE: b'new health'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = fixtures.PackagingTests().profile_stage(root)
            manifest = package.write_manifests(stage, 'a' * 40, 'b' * 40, None, 'task-results-ui', baseline)
            self.assertEqual(manifest['api_sha256'], {})
            self.assertEqual(manifest['previous_api_sha256'], {})
            runtime = root / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, 'a' * 40)
            manifest['api_sha256'] = {package.API_FILE: 'c' * 64}
            (stage / 'release.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                checker.verify(runtime, stage, 'a' * 40)

    def test_publication_and_recovery_never_touch_api_or_skip_paired_backup(self):
        fixtures, publisher = load('test_publish'), load('publish')
        for failure in (None, 'backup', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host = fixtures.FakeHost()
                host.release.update(profile='task-results-ui', api_sha256={}, previous_api_sha256={},
                    preserved_api_sha256={fixtures.MODULE: fixtures.OLD, fixtures.HEALTH_MODULE: fixtures.OLD})
                for root in host.file_state['api']:
                    root[fixtures.HEALTH_MODULE] = fixtures.OLD
                host.original_files = copy.deepcopy(host.file_state)
                host.fail = failure
                result = publisher.publish_release(host, fixtures.REVISION, fixtures.PREVIOUS)
                self.assertEqual(result['published'], failure is None, result)
                self.assertEqual(host.file_state['api'], host.original_files['api'])
                self.assertNotIn('api-publish', host.events)
                self.assertNotIn('api-restore', host.events)
                self.assertIn('paired-backup', host.events)
                self.assertTrue(host.health)

    def test_load_stage_requires_full_api_preservation_receipt_and_refuses_api_helpers(self):
        fixtures, publisher = load('test_publish'), load('publish')
        fixture = fixtures.FileContractTests()
        fixture.setUp()
        try:
            stage = fixture.operations_ui_stage()
            release = json.loads((stage / 'release.json').read_text())
            release['profile'] = 'task-results-ui'
            fixture.rewrite_stage(stage, release)
            self.assertEqual(publisher.load_stage(stage, fixtures.REVISION, fixtures.PREVIOUS)['api_sha256'], {})
            host = publisher.DockerHost(fixtures.REVISION, fixture.root)
            host.release = release
            for method in (host.helper, host.verify_receipt):
                with self.assertRaises(publisher.ReleaseFailure):
                    method('api', {})
            receipt = stage / 'isolated-checks.json'
            checks = json.loads(receipt.read_text())
            checks.pop('unchanged_api_tree_sha256')
            receipt.write_text(json.dumps(checks))
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.load_stage(stage, fixtures.REVISION, fixtures.PREVIOUS)
        finally:
            fixture.doCleanups()
