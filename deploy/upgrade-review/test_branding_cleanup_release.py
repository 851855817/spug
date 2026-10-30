"""Branding cleanup is an exact UI overlay with no API changes, including tests."""
import copy
import hashlib
import importlib.util
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
PROFILE = 'branding-cleanup-ui'
REVISION, PREVIOUS = 'a' * 40, 'b' * 40
PHP = 'apps/baota/operations/php_update.py'
HEALTH = 'apps/setting/health.py'
UI = {
    'spug_web/src/branding/LegalNotice.js',
    'spug_web/src/pages/system/setting/About.js',
    'spug_web/src/pages/system/setting/BasicSetting.js',
    'spug_web/src/pages/system/setting/KeySetting.js',
    'spug_web/src/pages/system/setting/LDAPSetting.js',
    'spug_web/src/pages/baota/PanelForm.js',
    'spug_web/src/pages/host/Import.js',
    'spug_web/src/pages/schedule/Step3.js',
    'spug_web/src/pages/deploy/app/AddSelect.js',
    'spug_web/src/pages/deploy/app/Ext1Setup2.js',
    'spug_web/src/pages/deploy/app/Ext1Setup3.js',
    'spug_web/src/pages/deploy/app/Ext2Setup2.js',
    'spug_web/src/locales/en/system_setting.js',
    'spug_web/src/locales/en/host.js',
    'spug_web/src/locales/en/schedule.js',
    'spug_web/src/locales/en/deploy_app.js',
}


def load(name):
    spec = importlib.util.spec_from_file_location('branding_cleanup_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrandingCleanupReleaseTests(unittest.TestCase):
    profile = PROFILE
    ui = UI
    ui_count = 16

    def make_stage(self, folder):
        return load('test_appearance_release').AppearanceReleaseTests().make_stage(folder)

    def manifest(self, stage):
        return load('package').write_manifests(stage, REVISION, PREVIOUS, None, self.profile,
            {PHP: b'php', HEALTH: b'health'})

    def receipt(self, stage, release, **changes):
        load('test_appearance_release').AppearanceReleaseTests().receipt(stage, release, **changes)

    def test_exact_ui_scope_rejects_every_api_path_and_extra_runtime_or_dependency(self):
        package = load('package')
        self.assertEqual(len(self.ui), self.ui_count)
        package.validate_runtime_changes(sorted(self.ui) + ['docs/guide.md',
            'spug_web/src/branding/branding.test.js'], self.profile)
        invalid = [[], sorted(self.ui)[1:], sorted(self.ui) + ['spug_web/src/routes.js'],
                   sorted(self.ui) + ['spug_web/package.json'], sorted(self.ui) + ['spug_web/package-lock.json']]
        invalid.extend(sorted(self.ui) + [name] for name in (
            'spug_api/apps/setting/appearance.py', 'spug_api/apps/setting/urls.py',
            'spug_api/spug/settings.py', 'spug_api/apps/account/models.py',
            'spug_api/apps/setting/test_appearance.py', 'spug_api/spug/test_urls.py',
            'spug_api/apps/setting/migrations/0002_cleanup.py',
            'spug_api/apps/setting/migrations/test_hidden.py', 'spug_api/test_anything.py'))
        for names in invalid:
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, self.profile)
        for old_profile in ('branding-ui', 'refined-palettes', 'site-appearance'):
            with self.subTest(profile=old_profile), self.assertRaises(ValueError):
                package.validate_runtime_changes(sorted(self.ui), old_profile)

    def test_empty_overlay_manifest_and_image_require_preserved_php_and_health(self):
        package, checker = load('package'), load('check_image')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.assertEqual(release['api_sha256'], {})
            self.assertEqual(release['previous_api_sha256'], {})
            self.assertEqual(set(release['preserved_api_sha256']), {PHP, HEALTH})
            for previous in (b'old module', {}, {PHP: b'php'}):
                with self.subTest(previous=previous), self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, previous, self.profile,
                        {PHP: b'php', HEALTH: b'health'})
            for preserved in (None, {}, {PHP: b'php'}, {PHP: b'php', HEALTH: b'drift'},
                              {PHP: b'php', HEALTH: b'health', 'extra.py': b'extra'}):
                with self.subTest(preserved=preserved), self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, None, self.profile, preserved)
            runtime = Path(folder) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, REVISION)
            for change in ({'api_sha256': {PHP: 'a' * 64}}, {'previous_api_sha256': {HEALTH: 'a' * 64}},
                           {'preserved_api_sha256': {}}, {'preserved_api_sha256': {PHP: 'a' * 64}}):
                (stage / 'release.json').write_text(json.dumps(dict(release, **change)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, REVISION)
            (stage / 'release.json').write_text(json.dumps(release))
            for directory in (runtime / 'spug_api', stage / 'source/spug_api'):
                for name in (PHP, HEALTH):
                    target = directory / name
                    original = target.read_bytes()
                    target.write_bytes(b'drift')
                    with self.subTest(directory=directory, name=name), self.assertRaises(ValueError):
                        checker.verify(runtime, stage, REVISION)
                    target.write_bytes(original)

    def test_package_cli_builds_full_static_ui_without_any_api_docker_copy(self):
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
                    return '\n'.join(sorted(self.ui)).encode()
                if values[0] == 'ls-files':
                    return '\0'.join('spug_api/' + name for name in (PHP, HEALTH)).encode()
                if values[0] == 'show':
                    return {PHP: b'php', HEALTH: b'health'}[values[1].split(':spug_api/')[1]]
                self.fail('unexpected Git command')
            with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                    mock.patch.object(sys, 'argv', ['package.py', PREVIOUS, '--node', 'node', '--profile', self.profile]), \
                    mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                    mock.patch.object(package.subprocess, 'run'), mock.patch('builtins.print'):
                package.main()
            packaged = root / ('upgrade-' + REVISION[:7])
            release = json.loads((packaged / 'release.json').read_text())
            self.assertEqual(release['api_sha256'], {})
            self.assertEqual(release['previous_api_sha256'], {})
            self.assertEqual([values[1] for values in calls if values[0] == 'show'], [
                PREVIOUS + ':spug_api/' + PHP, PREVIOUS + ':spug_api/' + HEALTH])
            self.assertEqual((packaged / 'Dockerfile').read_text().splitlines(), [
                'FROM spug-baota:' + PREVIOUS[:7], 'ARG SOURCE_REVISION',
                'LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"',
                'COPY build/ /opt/spug/spug_web/build/'])

    def test_prepare_preserves_complete_tree_using_immutable_image_ids_and_rejects_tag_drift(self):
        for failure in (None, 'changed-api', 'extra-api', 'missing-api', 'baseline-moved',
                        'candidate-moved', 'regression-failed'):
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
                baseline = dict(release['preserved_api_sha256'], **{
                    'apps/setting/appearance.py': '1' * 64, 'apps/setting/urls.py': '2' * 64,
                    'spug/settings.py': '3' * 64, 'config/local.ini': 'f' * 64})
                reads, probed = {'old': 0, 'new': 0}, []
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
                    result = ''
                    if command[-2] == '-':
                        probed.append(command[-3])
                        snapshot = dict(baseline)
                        if command[-3] == image_id:
                            if failure == 'changed-api':
                                snapshot['config/local.ini'] = '0' * 64
                            elif failure == 'extra-api':
                                snapshot['extra.py'] = '0' * 64
                            elif failure == 'missing-api':
                                del snapshot['apps/setting/appearance.py']
                        result = json.dumps(snapshot)
                    if 'apps.setting.test_appearance' in command and failure == 'regression-failed':
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
                    checks = json.loads(receipt.read_text())
                    self.assertEqual(checks['baseline_image_id'], baseline_id)
                    self.assertEqual(checks['unchanged_api_tree_sha256'], prepare.unchanged_api_digest(baseline, baseline))
                    loaded = load('publish').load_stage(receipt.parent, REVISION, PREVIOUS)
                    self.assertEqual(loaded['prepared_baseline_image_id'], baseline_id)
                    self.assertEqual(loaded['api_sha256'], {})

    def test_publish_requires_preservation_receipt_empty_overlays_and_exact_preserved_modules(self):
        publisher = load('publish')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.receipt(stage, release)
            self.assertEqual(publisher.load_stage(stage, REVISION, PREVIOUS)['api_sha256'], {})
            for key in ('baseline_image_id', 'unchanged_api_tree_sha256'):
                for value in (None, '', 'invalid'):
                    self.receipt(stage, release, **{key: value})
                    with self.subTest(key=key, value=value), self.assertRaises(publisher.ReleaseFailure):
                        publisher.load_stage(stage, REVISION, PREVIOUS)
            self.receipt(stage, release, baseline_image_id='sha256:' + 'c' * 64)
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.load_stage(stage, REVISION, PREVIOUS)
            for change in ({'api_sha256': {PHP: 'a' * 64}}, {'previous_api_sha256': {HEALTH: 'a' * 64}},
                           {'preserved_api_sha256': {}}, {'preserved_api_sha256': {PHP: 'a' * 64}}):
                self.receipt(stage, dict(release, **change))
                with self.subTest(change=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)
            self.receipt(stage, release)
            (stage / 'isolated-checks.json').unlink()
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_host_rechecks_immutable_baseline_and_candidate_and_refuses_api_helpers_or_receipts(self):
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
            host.run = mock.Mock(side_effect=AssertionError('API helper must not execute'))
            host.probe = mock.Mock(side_effect=AssertionError('API receipt must not execute'))
            for mode in ('publish', 'restore'):
                with self.subTest(mode=mode), self.assertRaises(publisher.ReleaseFailure):
                    host.helper('api', mode)
            with self.assertRaises(publisher.ReleaseFailure):
                host.verify_receipt('api', {})
            host.run.assert_not_called()
            host.probe.assert_not_called()

    def cleanup_host(self):
        fixture = load('test_publish')
        case = fixture.ControllerTests()
        case.setUp()
        case.operations_ui_profile()
        case.host.release['profile'] = self.profile
        return case.host, fixture

    def test_ui_only_publication_and_partial_failure_preserve_all_api_and_business(self):
        publisher = load('publish')
        for failure in (None, 'backup', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host, fixture = self.cleanup_host()
                host.fail = failure
                before_api, before_business = copy.deepcopy(host.file_state['api']), copy.deepcopy(host.business)
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertEqual(result['published'], failure is None, result)
                self.assertEqual(host.business, before_business)
                self.assertEqual(host.file_state['api'], before_api)
                self.assertFalse(any(event.startswith('api-') for event in host.events), host.events)
                self.assertIn('paired-backup', host.events)
                if failure is None:
                    self.assertLess(host.events.index('paired-backup'), host.events.index('ui-publish'))
                    self.assertIn('ui-verify-backup-receipt', host.events)
                else:
                    self.assertTrue(result['rolled_back'], result)
                    self.assertEqual(host.file_state, host.original_files)
                self.assertTrue(host.ingress and host.health)

    def test_preserved_runtime_drift_fails_before_maintenance_and_bad_ui_receipt_stays_closed(self):
        publisher = load('publish')
        for index in range(2):
            for name in (PHP, HEALTH):
                host, fixture = self.cleanup_host()
                host.file_state['api'][index][name] = fixture.NEW
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertFalse(result['published'])
                self.assertEqual(result['preflight_check'], 'runtime-baseline')
                self.assertNotIn('close', host.events)
        host, fixture = self.cleanup_host()
        host.fail = 'ui-incomplete-receipt'
        result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
        self.assertFalse(result['published'])
        self.assertFalse(result['rolled_back'])
        self.assertTrue(result['maintenance_closed'])
        self.assertFalse(host.ingress or host.health)
        self.assertFalse(any(event.startswith('api-') for event in host.events), host.events)


if __name__ == '__main__':
    unittest.main()
