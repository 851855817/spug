"""Fixed one-click login release scope, new-file prestate and reversible overlays."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
LOGIN = 'apps/baota/login_views.py'
URLS = 'apps/baota/urls.py'
PHP = 'apps/baota/operations/php_update.py'
HEALTH = 'apps/setting/health.py'
UI = {'spug_web/src/pages/baota/index.js', 'spug_web/src/pages/system/role/codes.js'}


def load(name):
    spec = importlib.util.spec_from_file_location('login_release_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PanelLoginReleaseTests(unittest.TestCase):
    def test_scope_accepts_exact_login_modules_and_ui_and_rejects_unrelated_runtime(self):
        package = load('package')
        names = ['spug_api/' + name for name in (LOGIN, URLS)] + list(UI)
        package.validate_runtime_changes(names + ['spug_api/apps/baota/test_login.py'], 'panel-login')
        for invalid in (names[:-1], names + ['spug_api/apps/baota/models.py'],
                        names + ['spug_web/src/routes.js'], list(UI), names + ['spug_api/' + PHP]):
            with self.subTest(names=invalid), self.assertRaises(ValueError):
                package.validate_runtime_changes(invalid, 'panel-login')

    def make_stage(self, folder):
        stage = Path(folder) / 'stage'
        for name, value in {LOGIN: b'new login', URLS: b'new urls', PHP: b'php', HEALTH: b'health'}.items():
            path = stage / 'source/spug_api' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        (stage / 'build').mkdir()
        for name in ('index.html', 'asset-manifest.json'):
            (stage / 'build' / name).write_bytes(b'{}')
        for name in ('apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'):
            (stage / name).write_bytes(b'# helper')
        return stage

    def test_new_login_module_has_explicit_absent_prestate_only_urls_may_have_old_hash(self):
        package = load('package')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            previous = {LOGIN: None, URLS: b'old urls'}
            preserved = {PHP: b'php', HEALTH: b'health'}
            release = package.write_manifests(stage, 'a' * 40, 'b' * 40, previous, 'panel-login', preserved)
            self.assertEqual(set(release['api_sha256']), {LOGIN, URLS})
            self.assertEqual(release['previous_api_sha256'],
                             {LOGIN: None, URLS: hashlib.sha256(b'old urls').hexdigest()})
            self.assertEqual(set(release['preserved_api_sha256']), {PHP, HEALTH})
            for invalid in ({LOGIN: b'already exists', URLS: b'old'}, {LOGIN: None, URLS: None}, {},
                            dict(previous, extra=None)):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    package.write_manifests(stage, 'a' * 40, 'b' * 40, invalid, 'panel-login', preserved)
            runtime = Path(folder) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            load('check_image').verify(runtime, stage, 'a' * 40)
            for key, invalid in (('previous_api_sha256', {LOGIN: 'c' * 64, URLS: 'd' * 64}),
                                 ('api_sha256', {LOGIN: None, URLS: 'd' * 64})):
                (stage / 'release.json').write_text(json.dumps(dict(release, **{key: invalid})))
                with self.assertRaises(ValueError):
                    load('check_image').verify(runtime, stage, 'a' * 40)

    def test_actual_overlay_restores_urls_and_removes_new_login_module_in_both_roots(self):
        # Execute the real fixed overlay with only its absolute storage roots substituted.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.make_stage(root)
            roots = [root / 'startup', root / 'live']
            for directory in roots:
                path = directory / URLS
                path.parent.mkdir(parents=True)
                path.write_bytes(b'old urls')
            hashes = {name: hashlib.sha256((stage / 'source/spug_api' / name).read_bytes()).hexdigest()
                      for name in (LOGIN, URLS)}
            (stage / 'api-release.json').write_text(json.dumps({'revision': 'a' * 40, 'sha256': hashes}))
            source = (HERE.parent / 'expiry/apply-expiry.py').read_text()
            source = source.replace("Path('/tmp/spug-ui-' + revision[:7])", 'Path(' + repr(str(stage)) + ')')
            source = source.replace("[Path('/opt/spug/spug_api'), Path('/data/spug/spug_api')]",
                                    '[Path(' + repr(str(roots[0])) + '), Path(' + repr(str(roots[1])) + ')]')
            source = source.replace("Path('/data/spug-api-backups')", 'Path(' + repr(str(root / 'backups')) + ')')
            for mode in ('publish', 'restore'):
                with mock.patch.object(sys, 'argv', ['overlay', 'a' * 40, mode]), contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(source, '<isolated-overlay>', 'exec'), {'__name__': '__main__'})
                for directory in roots:
                    self.assertEqual((directory / URLS).read_bytes(), b'new urls' if mode == 'publish' else b'old urls')
                    self.assertEqual((directory / LOGIN).exists(), mode == 'publish')

    def test_package_cli_reads_absent_baseline_and_copies_only_two_login_modules(self):
        package = load('package')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.make_stage(root)
            repo = root / 'repo'
            shutil.copytree(stage / 'source/spug_api', repo / 'spug_api')
            shutil.copytree(stage / 'build', repo / 'spug_web/build')
            for name in ('expiry/apply-expiry.py', 'upgrade-review/publish-ui.py',
                         'upgrade-review/fingerprint.py', 'inventory/release_safety.py'):
                path = repo / 'deploy' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'# helper')
            calls = []
            def git(command, **kwargs):
                values = command[1:]
                calls.append(values)
                if values == ['rev-parse', 'HEAD']:
                    return b'a' * 40
                if values[:2] == ['rev-parse', '--verify']:
                    return b'b' * 40
                if values[0] in ('status', 'ls-tree'):
                    return b''
                if values[0] == 'diff':
                    return '\n'.join(['spug_api/' + name for name in (LOGIN, URLS)] + list(UI)).encode()
                if values[0] == 'ls-files':
                    return '\n'.join('spug_api/' + name for name in (LOGIN, URLS, PHP, HEALTH)).encode()
                if values[0] == 'show':
                    return {URLS: b'old urls', PHP: b'php', HEALTH: b'health'}[values[1].split(':spug_api/')[1]]
                self.fail('unexpected Git command')
            with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                    mock.patch.object(sys, 'argv', ['package.py', 'b' * 40, '--node', 'node', '--profile', 'panel-login']), \
                    mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                    mock.patch.object(package.subprocess, 'run'), mock.patch('builtins.print'):
                package.main()
            packaged = root / ('upgrade-' + 'a' * 7)
            manifest = json.loads((packaged / 'release.json').read_text())
            self.assertIsNone(manifest['previous_api_sha256'][LOGIN])
            self.assertIn(['ls-tree', '--name-only', 'b' * 40, '--', 'spug_api/' + LOGIN], calls)
            copies = [line for line in (packaged / 'Dockerfile').read_text().splitlines() if line.startswith('COPY source/')]
            self.assertEqual(copies, ['COPY source/spug_api/' + name + ' /opt/spug/spug_api/' + name
                                      for name in (LOGIN, URLS)])

    def test_publisher_accepts_exact_module_count_and_absent_baseline_but_rejects_existing_file(self):
        publisher = load('publish')
        release = {'revision': 'a' * 40, 'previous_revision': 'b' * 40,
                   'api_sha256': {LOGIN: 'a' * 64, URLS: 'c' * 64},
                   'previous_api_sha256': {LOGIN: None, URLS: 'b' * 64}, 'ui_sha256': {}}
        baseline = {'api': [{URLS: 'b' * 64}, {URLS: 'b' * 64}], 'ui': [{}, {}],
                    'ui_revisions': ['b' * 40] * 2}
        publisher.verify_previous_files(baseline, release)
        publisher.check_helper_result({'revision': 'a' * 40, 'mode': 'publish', 'modules': 2}, 'api', 'publish', release)
        for count in (0, 1, 3):
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.check_helper_result({'revision': 'a' * 40, 'mode': 'publish', 'modules': count}, 'api', 'publish', release)
        baseline['api'][0][LOGIN] = 'd' * 64
        with self.assertRaises(publisher.ReleaseFailure):
            publisher.verify_previous_files(baseline, release)

    def test_publisher_stage_requires_fixed_overlay_absent_prestate_and_preserved_modules(self):
        package, publisher = load('package'), load('publish')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = package.write_manifests(stage, 'a' * 40, 'b' * 40,
                {LOGIN: None, URLS: b'old urls'}, 'panel-login', {PHP: b'php', HEALTH: b'health'})
            def receipt(manifest):
                (stage / 'release.json').write_text(json.dumps(manifest))
                check = stage / 'isolated-checks.json'
                check.unlink(missing_ok=True)
                archive = stage.with_suffix('.tar.gz')
                with tarfile.open(archive, 'w:gz') as output:
                    output.add(stage, arcname=stage.name)
                check.write_text(json.dumps({'revision': 'a' * 40, 'previous_revision': 'b' * 40,
                    'image': release['image'], 'schema_changes': False, 'linux_regression': True,
                    'release_failure_tests': True, 'image_wiring': True, 'image_id': 'sha256:' + 'c' * 64,
                    'archive_sha256': publisher.digest(archive)}))
            receipt(release)
            loaded = publisher.load_stage(stage, 'a' * 40, 'b' * 40)
            self.assertIsNone(loaded['previous_api_sha256'][LOGIN])
            for change in ({'previous_api_sha256': {LOGIN: 'a' * 64, URLS: 'b' * 64}},
                           {'previous_api_sha256': {LOGIN: None, URLS: None}},
                           {'api_sha256': {LOGIN: None, URLS: 'c' * 64}},
                           {'api_sha256': dict(release['api_sha256'], **{PHP: 'a' * 64})},
                           {'preserved_api_sha256': {}}, {'profile': 'unknown'}):
                with self.subTest(change=change):
                    receipt(dict(release, **change))
                    with self.assertRaises(publisher.ReleaseFailure):
                        publisher.load_stage(stage, 'a' * 40, 'b' * 40)

    def test_full_publisher_recovery_preserves_business_and_removes_new_file(self):
        fixture, publisher = load('test_publish'), load('publish')
        for failure in (None, 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host = fixture.FakeHost()
                host.fail = failure
                for root in host.file_state['api']:
                    root[URLS] = fixture.OLD
                    root[HEALTH] = fixture.OLD
                host.original_files = copy.deepcopy(host.file_state)
                host.release.update(profile='panel-login',
                    api_sha256={LOGIN: fixture.NEW, URLS: fixture.NEW},
                    previous_api_sha256={LOGIN: None, URLS: fixture.OLD},
                    preserved_api_sha256={PHP: fixture.OLD, HEALTH: fixture.OLD})
                original_helper = host.helper
                def helper(kind, mode):
                    result = original_helper(kind, mode)
                    if kind == 'api':
                        result['modules'] = 2
                    return result
                host.helper = helper
                business = copy.deepcopy(host.business)
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertEqual(host.business, business)
                if failure is None:
                    self.assertTrue(result['published'], result)
                    self.assertTrue(all(root[LOGIN] == fixture.NEW for root in host.file_state['api']))
                else:
                    self.assertTrue(result['rolled_back'], result)
                    self.assertEqual(host.file_state, host.original_files)
