"""Single existing SSH probe compatibility patch, reversible without UI or schema changes."""
import contextlib
import io
import sys
from unittest import mock
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).parent
MODULE = 'apps/monitor/ssl/remote_probe.py'
UI = set()
PROFILE = 'ssl-ssh-python36'


def load(name):
    spec = importlib.util.spec_from_file_location('ssl_python36_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SSLPython36ReleaseTests(unittest.TestCase):
    def test_scope_requires_only_existing_probe_and_collector_tests(self):
        package = load('package')
        allowed = ['spug_api/' + MODULE] + sorted(UI)
        package.validate_runtime_changes(allowed + ['docs/filters.md',
            'spug_api/apps/monitor/ssl/test_collectors.py'], PROFILE)
        for names in ([], allowed[1:], allowed + ['spug_api/apps/monitor/ssl/collectors.py'], allowed + ['spug_api/apps/baota/inventory/models.py'],
                      allowed + ['spug_web/src/routes.js'], allowed + ['spug_web/package.json']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, PROFILE)

    def test_manifest_and_image_require_only_probe_overlay_and_both_preserved_modules(self):
        fixtures, package, checker = load('test_package'), load('package'), load('check_image')
        baseline = {package.API_FILE: b'preserved PHP', package.HEALTH_FILE: b'new health'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = fixtures.PackagingTests().profile_stage(root)
            target = stage / 'source/spug_api' / MODULE
            target.parent.mkdir(parents=True); target.write_bytes(b'new probe')
            release = package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old probe', PROFILE, baseline)
            self.assertEqual(release['api_sha256'], {MODULE: hashlib.sha256(b'new probe').hexdigest()})
            self.assertEqual(release['previous_api_sha256'], {MODULE: hashlib.sha256(b'old probe').hexdigest()})
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
                    package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old probe', PROFILE, preserved)

    def test_publisher_loads_the_fixed_overlay_and_rejects_missing_preservation(self):
        fixtures, publisher = load('test_publish'), load('publish')
        fixture = fixtures.FileContractTests(); fixture.setUp()
        try:
            stage = fixture.stage('host-report')
            for name in (fixtures.MODULE, fixtures.HEALTH_MODULE):
                (stage / 'source/spug_api' / name).write_bytes(b'old')
            target = stage / 'source/spug_api' / MODULE
            target.parent.mkdir(parents=True); target.write_bytes(b'new')
            release = json.loads((stage / 'release.json').read_text())
            release.update(profile=PROFILE, api_sha256={MODULE: fixtures.NEW}, previous_api_sha256={MODULE: fixtures.OLD},
                preserved_api_sha256={fixtures.MODULE: fixtures.OLD, fixtures.HEALTH_MODULE: fixtures.OLD})
            (stage / 'api-release.json').write_text(json.dumps({'revision': fixtures.REVISION, 'sha256': release['api_sha256']}))
            fixture.rewrite_stage(stage, release)
            checks = json.loads((stage / 'isolated-checks.json').read_text())
            checks.update(baseline_image_id='sha256:' + fixtures.OLD, unchanged_api_tree_sha256=fixtures.OLD)
            (stage / 'isolated-checks.json').write_text(json.dumps(checks))
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

    def test_image_tree_preserves_all_other_modules_and_rejects_wrong_prestate(self):
        prepare = load('prepare')
        before = {MODULE: 'a' * 64, 'apps/monitor/ssl/jobs.py': 'b' * 64, 'apps/account/views.py': 'c' * 64}
        after = dict(before, **{MODULE: 'd' * 64})
        old, new = {MODULE: before[MODULE]}, {MODULE: after[MODULE]}
        self.assertTrue(prepare.ssl_python36_api_digest(before, after, old, new))
        for changed in (dict(after, extra='e' * 64), {MODULE: 'd' * 64},
                        dict(after, **{'apps/account/views.py': 'e' * 64})):
            with self.assertRaises(ValueError):
                prepare.ssl_python36_api_digest(before, changed, old, new)
        for invalid in ({}, {MODULE: None}, {MODULE: 'e' * 64}, dict(old, extra='f' * 64)):
            with self.assertRaises(ValueError):
                prepare.ssl_python36_api_digest(before, after, invalid, new)

    def test_actual_overlay_partial_failure_restores_existing_probe_at_both_roots(self):
        for failure in (False, True):
            with self.subTest(partial_failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                stage = load('test_package').PackagingTests().profile_stage(root)
                target = stage / 'source/spug_api' / MODULE
                target.parent.mkdir(parents=True)
                target.write_bytes(b'new ' + MODULE.encode())
                (stage / 'api-release.json').write_text(json.dumps({'revision': ('a' * 40), 'sha256': {MODULE: hashlib.sha256(target.read_bytes()).hexdigest()}}))
                roots = [root / 'startup', root / 'live']
                preferences = root / 'preferences.sqlite'
                preferences.write_bytes(b'persisted source preferences')
                for directory in roots:
                    for name, content in ((MODULE, b'old probe'), ('apps/monitor/ssl/jobs.py', b'old jobs'),
                                          ('apps/monitor/ssl/models.py', b'old settings'), ('apps/baota/operations/php_update.py', b'php'), ('apps/setting/health.py', b'health')):
                        target = directory / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                source = (HERE.parent / 'expiry/apply-expiry.py').read_text()
                source = source.replace("Path('/tmp/spug-ui-' + revision[:7])", 'Path(' + repr(str(stage)) + ')')
                source = source.replace("[Path('/opt/spug/spug_api'), Path('/data/spug/spug_api')]",
                    repr(roots).replace('WindowsPath(', 'Path(').replace('PosixPath(', 'Path('))
                source = source.replace("Path('/data/spug-api-backups')", 'Path(' + repr(str(root / 'backups')) + ')')
                original_copy, failed = shutil.copyfile, []
                def copy_file(origin, target, *args, **kwargs):
                    if failure and not failed and Path(target) == roots[1] / 'apps/monitor/ssl/.remote_probe.py.release':
                        failed.append(True)
                        raise OSError('injected partial overlay failure')
                    return original_copy(origin, target, *args, **kwargs)
                def execute(mode):
                    with mock.patch.object(sys, 'argv', ['overlay', ('a' * 40), mode]), contextlib.redirect_stdout(io.StringIO()):
                        exec(compile(source, '<isolated-overlay>', 'exec'), {'__name__': '__main__'})
                with mock.patch.object(shutil, 'copyfile', side_effect=copy_file):
                    if failure:
                        with self.assertRaises(OSError):
                            execute('publish')
                        self.assertTrue(failed)
                    else:
                        execute('publish')
                        self.assertTrue(all((directory / MODULE).read_bytes() == b'new ' + MODULE.encode()
                                            for directory in roots))
                        execute('restore')
                receipt = json.loads((root / 'backups' / ('a' * 40) / 'complete.json').read_text())
                self.assertEqual(receipt['originals'], {str(index) + '/' + MODULE:
                    hashlib.sha256(b'old probe').hexdigest() for index in range(2)})
                for directory in roots:
                    self.assertEqual((directory / MODULE).read_bytes(), b'old probe')
                    self.assertEqual((directory / 'apps/monitor/ssl/jobs.py').read_bytes(), b'old jobs')
                    self.assertEqual((directory / 'apps/monitor/ssl/models.py').read_bytes(), b'old settings')
                    self.assertEqual((directory / 'apps/baota/operations/php_update.py').read_bytes(), b'php')
                    self.assertEqual((directory / 'apps/setting/health.py').read_bytes(), b'health')
                self.assertEqual(preferences.read_bytes(), b'persisted source preferences')
