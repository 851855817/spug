"""Palette extensions replace one existing module and preserve the complete backend."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).parent
PROFILE = 'refined-palettes'
APPEARANCE = 'apps/setting/appearance.py'
URLS = 'apps/setting/urls.py'
SETTINGS = 'spug/settings.py'
PHP = 'apps/baota/operations/php_update.py'
HEALTH = 'apps/setting/health.py'
REVISION, PREVIOUS = 'a' * 40, 'b' * 40
UI = {
    'spug_web/src/appearance/presets.js',
    'spug_web/config-overrides.js',
    'spug_web/src/appearance/theme.less',
    'spug_web/src/appearance/AppearanceSetting.js',
    'spug_web/src/appearance/appearance.module.css',
}


def load(name):
    spec = importlib.util.spec_from_file_location('refined_palettes_release_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RefinedPalettesReleaseTests(unittest.TestCase):
    def make_stage(self, folder):
        return load('test_appearance_release').AppearanceReleaseTests().make_stage(folder)

    def manifest(self, stage):
        return load('package').write_manifests(stage, REVISION, PREVIOUS,
            b'old appearance', PROFILE, {PHP: b'php', HEALTH: b'health'})

    def receipt(self, stage, release, **changes):
        load('test_appearance_release').AppearanceReleaseTests().receipt(stage, release, **changes)

    def test_scope_requires_exact_five_ui_files_one_existing_api_and_only_appearance_api_tests(self):
        package = load('package')
        names = ['spug_api/' + APPEARANCE] + sorted(UI)
        package.validate_runtime_changes(names + ['spug_api/apps/setting/test_appearance.py',
            'spug_web/src/appearance/presets.test.js', 'docs/guide.md'], PROFILE)
        for invalid in (names[:-1], names[1:], names + ['spug_api/' + URLS],
                        names + ['spug_api/' + SETTINGS], names + ['spug_api/apps/setting/models.py'],
                        names + ['spug_api/apps/setting/migrations/0002_palette.py'],
                        names + ['spug_api/apps/setting/migrations/test_hidden.py'],
                        names + ['spug_api/apps/account/test_unreviewed.py'],
                        names + ['spug_api/spug/test_urls.py'], names + ['spug_web/src/appearance/store.js'],
                        names + ['spug_web/package.json'], names + ['spug_web/package-lock.json']):
            with self.subTest(names=invalid), self.assertRaises(ValueError):
                package.validate_runtime_changes(invalid, PROFILE)
        with self.assertRaises(ValueError):
            package.validate_runtime_changes(names, 'site-appearance')

    def test_manifest_and_image_require_existing_module_and_preserved_php_health(self):
        package, checker = load('package'), load('check_image')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.assertEqual(set(release['api_sha256']), {APPEARANCE})
            self.assertEqual(release['previous_api_sha256'], {
                APPEARANCE: hashlib.sha256(b'old appearance').hexdigest()})
            self.assertEqual(set(release['preserved_api_sha256']), {PHP, HEALTH})
            for invalid in (None, {}, {APPEARANCE: None}, {APPEARANCE: b'old appearance'}):
                with self.subTest(previous=invalid), self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, invalid, PROFILE,
                        {PHP: b'php', HEALTH: b'health'})
            for preserved in (None, {}, {PHP: b'php'}, {PHP: b'php', HEALTH: b'drift'}):
                with self.subTest(preserved=preserved), self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, b'old appearance', PROFILE, preserved)
            runtime = Path(folder) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, REVISION)
            for change in ({'previous_api_sha256': {APPEARANCE: None}},
                           {'api_sha256': dict(release['api_sha256'], **{URLS: 'f' * 64})},
                           {'preserved_api_sha256': {}}):
                (stage / 'release.json').write_text(json.dumps(dict(release, **change)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, REVISION)

    def test_full_image_tree_allows_only_existing_appearance_replacement_and_checks_prestate(self):
        prepare = load('prepare')
        self.assertTrue(hasattr(prepare, 'refined_palettes_api_digest'), 'palette overlay needs full-tree preservation')
        old, new = {APPEARANCE: 'a' * 64}, {APPEARANCE: 'b' * 64}
        baseline = {APPEARANCE: old[APPEARANCE], URLS: 'c' * 64, SETTINGS: 'd' * 64,
                    PHP: 'e' * 64, HEALTH: 'f' * 64, 'config/local.ini': '0' * 64}
        candidate = dict(baseline, **new)
        expected = prepare.unchanged_api_digest({name: value for name, value in baseline.items()
            if name != APPEARANCE}, {name: value for name, value in candidate.items() if name != APPEARANCE})
        self.assertEqual(prepare.refined_palettes_api_digest(baseline, candidate, old, new), expected)
        for name in (URLS, SETTINGS, PHP, HEALTH, 'config/local.ini', 'extra.py'):
            with self.subTest(drift=name), self.assertRaises(ValueError):
                prepare.refined_palettes_api_digest(baseline, dict(candidate, **{name: '1' * 64}), old, new)
        invalid = dict(candidate)
        del invalid[URLS]
        with self.assertRaises(ValueError):
            prepare.refined_palettes_api_digest(baseline, invalid, old, new)
        for invalid in ({}, {APPEARANCE: None}, {APPEARANCE: '0' * 64}, dict(old, **{URLS: 'c' * 64})):
            with self.subTest(previous=invalid), self.assertRaises(ValueError):
                prepare.refined_palettes_api_digest(baseline, candidate, invalid, new)
        for invalid in ({}, {APPEARANCE: None}, dict(new, **{URLS: 'c' * 64})):
            with self.subTest(overlay=invalid), self.assertRaises(ValueError):
                prepare.refined_palettes_api_digest(baseline, candidate, old, invalid)
        missing = dict(baseline)
        del missing[APPEARANCE]
        with self.assertRaises(ValueError):
            prepare.refined_palettes_api_digest(missing, candidate, old, new)

    def test_package_cli_reads_existing_module_and_builds_one_module_overlay_with_full_static_ui(self):
        package = load('package')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.make_stage(root)
            repo = root / 'repo'
            shutil.copytree(stage / 'source/spug_api', repo / 'spug_api')
            shutil.copytree(stage / 'build', repo / 'spug_web/build')
            for name in ('expiry/apply-expiry.py', 'upgrade-review/publish-ui.py',
                         'upgrade-review/fingerprint.py', 'inventory/release_safety.py'):
                target = repo / 'deploy' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'# helper')
            calls = []
            def git(command, **kwargs):
                values = command[1:]
                calls.append(values)
                if values == ['rev-parse', 'HEAD']:
                    return REVISION.encode()
                if values[:2] == ['rev-parse', '--verify']:
                    return PREVIOUS.encode()
                if values[0] == 'status':
                    return b''
                if values[0] == 'diff':
                    return '\n'.join(['spug_api/' + APPEARANCE] + sorted(UI)).encode()
                if values[0] == 'ls-files':
                    return '\0'.join('spug_api/' + name for name in (APPEARANCE, URLS, SETTINGS, PHP, HEALTH)).encode()
                if values[0] == 'show':
                    return {APPEARANCE: b'old appearance', PHP: b'php', HEALTH: b'health'}[
                        values[1].split(':spug_api/')[1]]
                self.fail('unexpected Git command')
            with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                    mock.patch.object(sys, 'argv', ['package.py', PREVIOUS, '--node', 'node', '--profile', PROFILE]), \
                    mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                    mock.patch.object(package.subprocess, 'run'), mock.patch('builtins.print'):
                package.main()
            packaged = root / ('upgrade-' + REVISION[:7])
            release = json.loads((packaged / 'release.json').read_text())
            self.assertEqual(release['previous_api_sha256'], {APPEARANCE: hashlib.sha256(b'old appearance').hexdigest()})
            self.assertIn(['show', PREVIOUS + ':spug_api/' + APPEARANCE], calls)
            self.assertFalse(any(command[0] == 'ls-tree' for command in calls))
            self.assertEqual((packaged / 'Dockerfile').read_text().splitlines(), [
                'FROM spug-baota:' + PREVIOUS[:7], 'ARG SOURCE_REVISION',
                'LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"',
                'COPY source/spug_api/' + APPEARANCE + ' /opt/spug/spug_api/' + APPEARANCE,
                'COPY build/ /opt/spug/spug_web/build/'])

    def test_prepare_binds_full_tree_and_both_immutable_image_identities_before_receipt(self):
        for failure in (None, 'backend-drift', 'wrong-prestate', 'baseline-moved', 'candidate-moved', 'appearance-test-failed'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                prepare = load('prepare')
                root = Path(folder)
                stage = self.make_stage(root / 'fixture')
                source_probe = stage / 'source/deploy/upgrade-review/publish.py'
                source_probe.parent.mkdir(parents=True)
                shutil.copyfile(HERE / 'publish.py', source_probe)
                release = self.manifest(stage)
                archive = root / ('upgrade-' + REVISION[:7] + '.tar.gz')
                with tarfile.open(archive, 'w:gz') as output:
                    output.add(stage, arcname='upgrade-' + REVISION[:7])
                baseline_id, image_id = 'sha256:' + 'd' * 64, 'sha256:' + 'c' * 64
                baseline = dict(release['previous_api_sha256'], **release['preserved_api_sha256'])
                baseline.update({URLS: '1' * 64, SETTINGS: '2' * 64, 'config/local.ini': 'f' * 64})
                candidate = dict(baseline, **release['api_sha256'])
                reads, probed, commands = {'old': 0, 'new': 0}, [], []
                def inspect(command, **kwargs):
                    old = command[-1] == 'spug-baota:' + PREVIOUS[:7]
                    kind = 'old' if old else 'new'
                    reads[kind] += 1
                    identity = baseline_id if old else image_id
                    if reads[kind] == 2 and failure == ('baseline-moved' if old else 'candidate-moved'):
                        identity = 'sha256:' + 'e' * 64
                    return json.dumps([{'Id': identity, 'Config': {'Labels': {
                        'org.opencontainers.image.revision': PREVIOUS if old else REVISION}}}])
                def run(command, **kwargs):
                    commands.append(command)
                    result = ''
                    if command[-2] == '-':
                        probed.append(command[-3])
                        snapshot = dict(baseline if command[-3] == baseline_id else candidate)
                        if command[-3] == image_id and failure == 'backend-drift':
                            snapshot[URLS] = '0' * 64
                        if command[-3] == baseline_id and failure == 'wrong-prestate':
                            snapshot[APPEARANCE] = '0' * 64
                        result = json.dumps(snapshot)
                    if 'apps.setting.test_appearance' in command and failure == 'appearance-test-failed':
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0, stdout=result)
                with mock.patch.object(prepare, 'Path', side_effect=lambda value: root if value == '/opt/spug' else Path(value)), \
                        mock.patch.object(prepare.subprocess, 'check_output', side_effect=inspect), \
                        mock.patch.object(prepare.subprocess, 'run', side_effect=run), \
                        mock.patch.object(prepare.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=3 * 1024**3)), \
                        mock.patch.object(sys, 'argv', ['prepare.py', REVISION, hashlib.sha256(archive.read_bytes()).hexdigest()]), \
                        mock.patch('builtins.print'):
                    if failure:
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            prepare.main()
                    else:
                        prepare.main()
                receipt = root / ('upgrade-' + REVISION[:7]) / 'isolated-checks.json'
                self.assertEqual(receipt.exists(), failure is None)
                self.assertEqual(probed, [baseline_id, image_id])
                if failure is None:
                    self.assertTrue(any('apps.setting.test_appearance' in command for command in commands))
                    loaded = load('publish').load_stage(receipt.parent, REVISION, PREVIOUS)
                    self.assertEqual(loaded['prepared_baseline_image_id'], baseline_id)
                    self.assertEqual(loaded['api_sha256'], release['api_sha256'])

    def test_publisher_requires_full_tree_receipt_real_old_hash_and_preserved_modules(self):
        publisher = load('publish')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.receipt(stage, release)
            loaded = publisher.load_stage(stage, REVISION, PREVIOUS)
            self.assertEqual(loaded['prepared_baseline_image_id'], 'sha256:' + 'd' * 64)
            for change in ({'baseline_image_id': None}, {'unchanged_api_tree_sha256': None},
                           {'baseline_image_id': 'sha256:' + 'c' * 64}, {'unchanged_api_tree_sha256': 'invalid'}):
                self.receipt(stage, release, **change)
                with self.subTest(receipt=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)
            for change in ({'previous_api_sha256': {APPEARANCE: None}},
                           {'api_sha256': dict(release['api_sha256'], **{URLS: 'a' * 64})},
                           {'preserved_api_sha256': {}}):
                self.receipt(stage, dict(release, **change))
                with self.subTest(manifest=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)
            self.receipt(stage, release)
            (stage / 'isolated-checks.json').unlink()
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_host_rejects_candidate_or_baseline_image_tag_drift_before_publication(self):
        publisher = load('publish')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.receipt(stage, release)
            host = publisher.DockerHost(REVISION, Path(folder))
            host.stage = stage
            for failure in (None, 'candidate', 'baseline'):
                commands = []
                def run(command, **kwargs):
                    commands.append(command)
                    if '{{.Id}}' not in command:
                        return REVISION
                    old = command[-1] == 'spug-baota:' + PREVIOUS[:7]
                    if failure == ('baseline' if old else 'candidate'):
                        return 'sha256:' + 'e' * 64
                    return 'sha256:' + ('d' if old else 'c') * 64
                host.run = run
                with mock.patch.object(publisher, 'available_memory', return_value=512 * 1024**2), \
                        mock.patch.object(publisher.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=3 * 1024**3)):
                    if failure:
                        with self.assertRaises(publisher.ReleaseFailure):
                            host.load_release(REVISION, PREVIOUS)
                    else:
                        host.load_release(REVISION, PREVIOUS)
                        self.assertTrue(any(command[-1] == 'spug-baota:' + PREVIOUS[:7] for command in commands))

    def test_actual_overlay_partial_failure_restores_existing_appearance_at_both_roots(self):
        for failure in (False, True):
            with self.subTest(partial_failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                stage = self.make_stage(root)
                self.manifest(stage)
                roots = [root / 'startup', root / 'live']
                preferences = root / 'preferences.sqlite'
                preferences.write_bytes(b'persisted palette preferences')
                for directory in roots:
                    for name, content in ((APPEARANCE, b'old appearance'), (URLS, b'old urls'),
                                          (SETTINGS, b'old settings'), (PHP, b'php'), (HEALTH, b'health')):
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
                    if failure and not failed and Path(target) == roots[1] / 'apps/setting/.appearance.py.release':
                        failed.append(True)
                        raise OSError('injected partial overlay failure')
                    return original_copy(origin, target, *args, **kwargs)
                def execute(mode):
                    with mock.patch.object(sys, 'argv', ['overlay', REVISION, mode]), contextlib.redirect_stdout(io.StringIO()):
                        exec(compile(source, '<isolated-overlay>', 'exec'), {'__name__': '__main__'})
                with mock.patch.object(shutil, 'copyfile', side_effect=copy_file):
                    if failure:
                        with self.assertRaises(OSError):
                            execute('publish')
                        self.assertTrue(failed)
                    else:
                        execute('publish')
                        self.assertTrue(all((directory / APPEARANCE).read_bytes() == b'new ' + APPEARANCE.encode()
                                            for directory in roots))
                        execute('restore')
                receipt = json.loads((root / 'backups' / REVISION / 'complete.json').read_text())
                self.assertEqual(receipt['originals'], {str(index) + '/' + APPEARANCE:
                    hashlib.sha256(b'old appearance').hexdigest() for index in range(2)})
                for directory in roots:
                    self.assertEqual((directory / APPEARANCE).read_bytes(), b'old appearance')
                    self.assertEqual((directory / URLS).read_bytes(), b'old urls')
                    self.assertEqual((directory / SETTINGS).read_bytes(), b'old settings')
                    self.assertEqual((directory / PHP).read_bytes(), b'php')
                    self.assertEqual((directory / HEALTH).read_bytes(), b'health')
                self.assertEqual(preferences.read_bytes(), b'persisted palette preferences')

    def palette_host(self):
        fixture = load('test_publish')
        host = fixture.FakeHost()
        for root in host.file_state['api']:
            root.update({APPEARANCE: fixture.OLD, URLS: fixture.OLD, SETTINGS: fixture.OLD, HEALTH: fixture.OLD})
        host.original_files = copy.deepcopy(host.file_state)
        host.release.update(profile=PROFILE, api_sha256={APPEARANCE: fixture.NEW},
            previous_api_sha256={APPEARANCE: fixture.OLD}, preserved_api_sha256={PHP: fixture.OLD, HEALTH: fixture.OLD})
        host.business['appearance_preferences'] = {'count': 3, 'digest': fixture.OLD}
        return host, fixture

    def test_publication_and_failure_preserve_preferences_and_restore_existing_module(self):
        publisher = load('publish')
        for failure in (None, 'backup', 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host, fixture = self.palette_host()
                host.fail = failure
                before = copy.deepcopy(host.business)
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertEqual(host.business, before)
                self.assertIn('paired-backup', host.events)
                self.assertEqual(result['published'], failure is None, result)
                if failure is None:
                    self.assertLess(host.events.index('paired-backup'), host.events.index('api-publish'))
                    self.assertIn('api-verify-backup-receipt', host.events)
                else:
                    self.assertTrue(result['rolled_back'], result)
                    self.assertEqual(host.file_state, host.original_files)
                    self.assertTrue(all(root[APPEARANCE] == fixture.OLD for root in host.file_state['api']))

    def test_runtime_missing_or_wrong_previous_module_fails_before_maintenance(self):
        publisher = load('publish')
        for index in range(2):
            for change in (None, 'f' * 64):
                host, fixture = self.palette_host()
                if change is None:
                    del host.file_state['api'][index][APPEARANCE]
                else:
                    host.file_state['api'][index][APPEARANCE] = change
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertFalse(result['published'])
                self.assertEqual(result['preflight_check'], 'runtime-baseline')
                self.assertNotIn('close', host.events)
                self.assertTrue(host.ingress and host.health)

    def test_incomplete_original_receipt_keeps_maintenance_closed(self):
        publisher = load('publish')
        for kind in ('api', 'ui'):
            host, fixture = self.palette_host()
            host.fail = kind + '-incomplete-receipt'
            result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
            self.assertFalse(result['published'])
            self.assertFalse(result['rolled_back'])
            self.assertTrue(result['maintenance_closed'])
            self.assertFalse(host.ingress or host.health)


if __name__ == '__main__':
    unittest.main()
