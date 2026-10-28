"""Appearance is a fixed overlay; all other runtime bytes remain protected."""
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
import unittest
from unittest import mock

HERE = Path(__file__).parent
PROFILE = 'site-appearance'
APPEARANCE = 'apps/setting/appearance.py'
URLS = 'apps/setting/urls.py'
SETTINGS = 'spug/settings.py'
MODULES = (APPEARANCE, URLS, SETTINGS)
PHP = 'apps/baota/operations/php_update.py'
HEALTH = 'apps/setting/health.py'
REVISION, PREVIOUS = 'a' * 40, 'b' * 40
UI = {
    'spug_web/config-overrides.js', 'spug_web/src/index.js', 'spug_web/src/index.less',
    'spug_web/src/pages/system/setting/index.js',
    'spug_web/src/appearance/presets.js', 'spug_web/src/appearance/store.js',
    'spug_web/src/appearance/Provider.js', 'spug_web/src/appearance/AppearanceSetting.js',
    'spug_web/src/appearance/appearance.module.css', 'spug_web/src/appearance/theme.less',
    'spug_web/src/branding/branding.module.css',
    'spug_web/src/components/ResizableTable.less', 'spug_web/src/components/index.module.less',
    'spug_web/src/components/reminderDismiss.module.css', 'spug_web/src/layout/layout.module.less',
    'spug_web/src/libs/libs.module.css', 'spug_web/src/pages/config/setting/index.module.less',
    'spug_web/src/pages/dashboard/index.module.css', 'spug_web/src/pages/deploy/app/index.module.css',
    'spug_web/src/pages/deploy/repository/index.module.less', 'spug_web/src/pages/deploy/request/console.module.less',
    'spug_web/src/pages/exec/panel/TaskDetail.module.css', 'spug_web/src/pages/exec/task/index.module.less',
    'spug_web/src/pages/exec/transfer/index.module.less', 'spug_web/src/pages/home/index.module.less',
    'spug_web/src/pages/host/index.module.less', 'spug_web/src/pages/host/selector.module.less',
    'spug_web/src/pages/login/login.module.css', 'spug_web/src/pages/monitor/index.module.less',
    'spug_web/src/pages/pipeline/console/body.module.less', 'spug_web/src/pages/pipeline/console/node.module.less',
    'spug_web/src/pages/pipeline/editor.module.less', 'spug_web/src/pages/pipeline/modules/index.module.less',
    'spug_web/src/pages/pipeline/node.module.less', 'spug_web/src/pages/pipeline/nodeConfig.module.less',
    'spug_web/src/pages/schedule/index.module.less', 'spug_web/src/pages/server-monitor/index.module.less',
    'spug_web/src/pages/ssh/index.module.less', 'spug_web/src/pages/ssh/setting.module.less',
    'spug_web/src/pages/system/health/index.module.css', 'spug_web/src/pages/system/role/index.module.css',
    'spug_web/src/pages/system/setting/index.module.css', 'spug_web/src/pages/welcome/info/index.module.css',
    'spug_web/src/components/AppSelector.js',
    'spug_web/src/components/ResourceMonitorButton.js',
    'spug_web/src/components/ResourceMonitorSwitch.js',
    'spug_web/src/components/TableCard.js',
    'spug_web/src/layout/Notification.js',
    'spug_web/src/pages/alarm/alarm/Table.js',
    'spug_web/src/pages/alarm/contact/Form.js',
    'spug_web/src/pages/config/environment/Table.js',
    'spug_web/src/pages/config/setting/TableView.js',
    'spug_web/src/pages/deploy/app/AddSelect.js',
    'spug_web/src/pages/deploy/app/AutoDeploy.js',
    'spug_web/src/pages/deploy/app/Ext1Setup2.js',
    'spug_web/src/pages/deploy/app/Table.js',
    'spug_web/src/pages/deploy/app/Tips.js',
    'spug_web/src/pages/deploy/repository/Console.js',
    'spug_web/src/pages/deploy/repository/Detail.js',
    'spug_web/src/pages/deploy/repository/Form.js',
    'spug_web/src/pages/deploy/repository/Table.js',
    'spug_web/src/pages/deploy/request/Console.js',
    'spug_web/src/pages/deploy/request/Ext1Form.js',
    'spug_web/src/pages/deploy/request/Ext2Form.js',
    'spug_web/src/pages/deploy/request/HostSelector.js',
    'spug_web/src/pages/deploy/request/Rollback.js',
    'spug_web/src/pages/deploy/request/Table.js',
    'spug_web/src/pages/exec/task/Output.js',
    'spug_web/src/pages/exec/task/index.js',
    'spug_web/src/pages/exec/transfer/Output.js',
    'spug_web/src/pages/exec/transfer/index.js',
    'spug_web/src/pages/home/Notice.js',
    'spug_web/src/pages/host/Group.js',
    'spug_web/src/pages/host/Import.js',
    'spug_web/src/pages/host/Selector.js',
    'spug_web/src/pages/host/Sync.js',
    'spug_web/src/pages/host/Table.js',
    'spug_web/src/pages/monitor/MonitorCard.js',
    'spug_web/src/pages/monitor/Step1.js',
    'spug_web/src/pages/pipeline/Icon.js',
    'spug_web/src/pages/pipeline/Node.js',
    'spug_web/src/pages/pipeline/Table.js',
    'spug_web/src/pages/pipeline/console/Body.js',
    'spug_web/src/pages/pipeline/modules/index.js',
    'spug_web/src/pages/schedule/Step2.js',
    'spug_web/src/pages/server-expiry/Editor.js',
    'spug_web/src/pages/ssh/FileManager.js',
    'spug_web/src/pages/ssh/index.js',
    'spug_web/src/pages/system/setting/BasicSetting.js',
    'spug_web/src/pages/system/setting/KeySetting.js',
}


def load(name):
    spec = importlib.util.spec_from_file_location('appearance_release_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AppearanceReleaseTests(unittest.TestCase):
    def make_stage(self, folder):
        stage = Path(folder) / 'stage'
        for name, content in {**{name: b'new ' + name.encode() for name in MODULES},
                              PHP: b'php', HEALTH: b'health'}.items():
            path = stage / 'source/spug_api' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (stage / 'build').mkdir()
        for name in ('index.html', 'asset-manifest.json'):
            (stage / 'build' / name).write_bytes(b'{}')
        for name in load('package').HELPERS:
            (stage / name).write_bytes(b'# helper')
        return stage

    def manifest(self, stage):
        return load('package').write_manifests(stage, REVISION, PREVIOUS,
            {APPEARANCE: None, URLS: b'old urls', SETTINGS: b'old settings'}, PROFILE,
            {PHP: b'php', HEALTH: b'health'})

    def receipt(self, stage, release, **changes):
        (stage / 'release.json').write_text(json.dumps(release))
        receipt = stage / 'isolated-checks.json'
        receipt.unlink(missing_ok=True)
        archive = stage.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as output:
            output.add(stage, arcname=stage.name)
        checks = {'revision': REVISION, 'previous_revision': PREVIOUS, 'image': release['image'],
                  'schema_changes': False, 'linux_regression': True, 'release_failure_tests': True,
                  'image_wiring': True, 'image_id': 'sha256:' + 'c' * 64,
                  'baseline_image_id': 'sha256:' + 'd' * 64, 'unchanged_api_tree_sha256': 'e' * 64,
                  'archive_sha256': load('publish').digest(archive)}
        checks.update(changes)
        receipt.write_text(json.dumps(checks))

    def test_scope_is_fixed_and_rejects_unrelated_runtime_permissions_tasks_schema_and_dependencies(self):
        package = load('package')
        self.assertEqual(package.APPEARANCE_UI, UI)
        names = ['spug_api/' + name for name in MODULES] + sorted(UI)
        package.validate_runtime_changes(names + ['spug_api/apps/setting/test_appearance.py',
            'spug_api/spug/test_urls.py', 'spug_web/src/appearance/store.test.js', 'docs/guide.md'], PROFILE)
        for invalid in (names[:-1], names + ['spug_api/apps/account/views.py'],
                        names + ['spug_api/apps/baota/tasks.py'], names + ['spug_api/apps/setting/models.py'],
                        names + ['spug_api/apps/setting/migrations/0002_appearance.py'],
                        names + ['spug_api/apps/setting/migrations/test_hidden_migration.py'],
                        names + ['spug_api/apps/account/test_unreviewed.py'],
                        names + ['spug_web/src/routes.js'], names + ['spug_web/package.json'],
                        names + ['spug_web/package-lock.json']):
            with self.subTest(names=invalid), self.assertRaises(ValueError):
                package.validate_runtime_changes(invalid, PROFILE)
        with self.assertRaises(ValueError):
            package.validate_runtime_changes(names, 'inventory-role-presets-ui')

    def test_manifest_and_image_require_new_module_absent_existing_modules_and_preserved_php_health(self):
        package, checker = load('package'), load('check_image')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.assertEqual(set(release['api_sha256']), set(MODULES))
            self.assertEqual(release['previous_api_sha256'], {APPEARANCE: None,
                URLS: hashlib.sha256(b'old urls').hexdigest(), SETTINGS: hashlib.sha256(b'old settings').hexdigest()})
            self.assertEqual(set(release['preserved_api_sha256']), {PHP, HEALTH})
            previous = {APPEARANCE: None, URLS: b'old urls', SETTINGS: b'old settings'}
            for invalid in (dict(previous, **{APPEARANCE: b'existing'}), dict(previous, **{URLS: None}),
                            dict(previous, **{SETTINGS: None}), {}, dict(previous, extra=None)):
                with self.subTest(previous=invalid), self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, invalid, PROFILE, {PHP: b'php', HEALTH: b'health'})
            runtime = Path(folder) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, REVISION)
            for change in ({'previous_api_sha256': dict(release['previous_api_sha256'], **{APPEARANCE: 'f' * 64})},
                           {'previous_api_sha256': dict(release['previous_api_sha256'], **{SETTINGS: None})},
                           {'api_sha256': dict(release['api_sha256'], **{PHP: 'f' * 64})},
                           {'preserved_api_sha256': {}}):
                (stage / 'release.json').write_text(json.dumps(dict(release, **change)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, REVISION)

    def test_full_image_tree_allows_only_exact_overlay_and_checks_prestate(self):
        prepare = load('prepare')
        self.assertTrue(hasattr(prepare, 'appearance_api_digest'), 'appearance needs full-tree image preservation')
        old = {APPEARANCE: None, URLS: 'a' * 64, SETTINGS: 'b' * 64}
        new = {name: 'c' * 64 for name in MODULES}
        baseline = {URLS: old[URLS], SETTINGS: old[SETTINGS], PHP: 'd' * 64,
                    HEALTH: 'e' * 64, 'config/local.ini': 'f' * 64}
        candidate = dict(baseline, **new)
        expected = prepare.unchanged_api_digest({name: value for name, value in baseline.items()
            if name not in MODULES}, {name: value for name, value in candidate.items() if name not in MODULES})
        self.assertEqual(prepare.appearance_api_digest(baseline, candidate, old, new), expected)
        for invalid in (dict(candidate, **{PHP: '0' * 64}), dict(candidate, extra='0' * 64),
                        {name: value for name, value in candidate.items() if name != 'config/local.ini'},
                        dict(candidate, **{SETTINGS: '0' * 64})):
            with self.subTest(candidate=invalid), self.assertRaises(ValueError):
                prepare.appearance_api_digest(baseline, invalid, old, new)
        with self.assertRaises(ValueError):
            prepare.appearance_api_digest(dict(baseline, **{APPEARANCE: '0' * 64}), candidate, old, new)
        with self.assertRaises(ValueError):
            prepare.appearance_api_digest(dict(baseline, **{URLS: '0' * 64}), candidate, old, new)

    def test_package_cli_reads_explicit_new_module_prestate_and_copies_only_three_modules(self):
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
                if values[0] in ('status', 'ls-tree'):
                    return b''
                if values[0] == 'diff':
                    return '\n'.join(['spug_api/' + name for name in MODULES] + sorted(package.APPEARANCE_UI)).encode()
                if values[0] == 'ls-files':
                    return '\0'.join('spug_api/' + name for name in (*MODULES, PHP, HEALTH)).encode()
                if values[0] == 'show':
                    return {URLS: b'old urls', SETTINGS: b'old settings', PHP: b'php', HEALTH: b'health'}[
                        values[1].split(':spug_api/')[1]]
                self.fail('unexpected Git command')
            with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                    mock.patch.object(sys, 'argv', ['package.py', PREVIOUS, '--node', 'node', '--profile', PROFILE]), \
                    mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                    mock.patch.object(package.subprocess, 'run'), mock.patch('builtins.print'):
                package.main()
            packaged = root / ('upgrade-' + REVISION[:7])
            release = json.loads((packaged / 'release.json').read_text())
            self.assertIsNone(release['previous_api_sha256'][APPEARANCE])
            self.assertIn(['ls-tree', '--name-only', PREVIOUS, '--', 'spug_api/' + APPEARANCE], calls)
            copies = [line for line in (packaged / 'Dockerfile').read_text().splitlines() if line.startswith('COPY source/')]
            self.assertEqual(copies, ['COPY source/spug_api/' + name + ' /opt/spug/spug_api/' + name for name in MODULES])

    def test_prepare_runs_appearance_regressions_and_preserves_full_tree_before_receipt(self):
        for failure in (None, 'backend-drift', 'baseline-moved', 'appearance-test-failed'):
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
                baseline = {name: value for name, value in release['previous_api_sha256'].items() if value is not None}
                baseline.update(release['preserved_api_sha256'])
                baseline['config/local.ini'] = 'f' * 64
                candidate = dict(baseline, **release['api_sha256'])
                baseline_reads, probed, commands = [], [], []
                def inspect(command, **kwargs):
                    old = command[-1] == 'spug-baota:' + PREVIOUS[:7]
                    if old:
                        baseline_reads.append(command)
                    identity = baseline_id if old else image_id
                    if old and len(baseline_reads) == 2 and failure == 'baseline-moved':
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
                            snapshot['config/local.ini'] = '0' * 64
                        result = json.dumps(snapshot)
                    if 'apps.setting.test_appearance' in command and failure == 'appearance-test-failed':
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0, stdout=result)
                with mock.patch.object(prepare, 'Path', side_effect=lambda value: root if value == '/opt/spug' else Path(value)), \
                        mock.patch.object(prepare.subprocess, 'check_output', side_effect=inspect), \
                        mock.patch.object(prepare.subprocess, 'run', side_effect=run), \
                        mock.patch.object(prepare.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 3 * 1024**3})()), \
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
                    self.assertTrue(any('apps.setting.test_appearance' in command and 'spug.test_urls' in command
                                        for command in commands), 'isolated appearance/auth regressions must run')
                    loaded = load('publish').load_stage(receipt.parent, REVISION, PREVIOUS)
                    self.assertEqual(loaded['prepared_baseline_image_id'], baseline_id)
                    self.assertEqual(loaded['api_sha256'], release['api_sha256'])

    def test_publisher_requires_preservation_receipt_and_exact_overlay(self):
        publisher = load('publish')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.receipt(stage, release)
            loaded = publisher.load_stage(stage, REVISION, PREVIOUS)
            self.assertEqual(loaded['prepared_baseline_image_id'], 'sha256:' + 'd' * 64)
            for change in ({'baseline_image_id': None}, {'unchanged_api_tree_sha256': None},
                           {'baseline_image_id': 'sha256:' + 'c' * 64}):
                self.receipt(stage, release, **change)
                with self.subTest(receipt=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)
            for change in ({'previous_api_sha256': dict(release['previous_api_sha256'], **{APPEARANCE: 'a' * 64})},
                           {'api_sha256': dict(release['api_sha256'], **{PHP: 'a' * 64})},
                           {'preserved_api_sha256': {}}):
                self.receipt(stage, dict(release, **change))
                with self.subTest(manifest=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_actual_overlay_receipt_restores_both_existing_modules_and_removes_new_module(self):
        for failure in (False, True):
            with self.subTest(partial_failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                stage = self.make_stage(root)
                self.manifest(stage)
                roots = [root / 'startup', root / 'live']
                for directory in roots:
                    for name, content in ((URLS, b'old urls'), (SETTINGS, b'old settings'), (PHP, b'php')):
                        target = directory / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                source = (HERE.parent / 'expiry/apply-expiry.py').read_text()
                source = source.replace("Path('/tmp/spug-ui-' + revision[:7])", 'Path(' + repr(str(stage)) + ')')
                source = source.replace("[Path('/opt/spug/spug_api'), Path('/data/spug/spug_api')]",
                    repr(roots).replace('WindowsPath(', 'Path(').replace('PosixPath(', 'Path('))
                source = source.replace("Path('/data/spug-api-backups')", 'Path(' + repr(str(root / 'backups')) + ')')
                original_copy = shutil.copyfile
                failed = []
                def copy(origin, target, *args, **kwargs):
                    if failure and not failed and Path(target) == roots[1] / 'apps/setting/.urls.py.release':
                        failed.append(True)
                        raise OSError('injected partial overlay failure')
                    return original_copy(origin, target, *args, **kwargs)
                def execute(mode):
                    with mock.patch.object(sys, 'argv', ['overlay', REVISION, mode]), contextlib.redirect_stdout(io.StringIO()):
                        exec(compile(source, '<isolated-overlay>', 'exec'), {'__name__': '__main__'})
                with mock.patch.object(shutil, 'copyfile', side_effect=copy):
                    if failure:
                        with self.assertRaises(OSError):
                            execute('publish')
                        self.assertTrue(failed)
                    else:
                        execute('publish')
                        self.assertTrue(all((directory / APPEARANCE).exists() for directory in roots))
                        execute('restore')
                receipt = json.loads((root / 'backups' / REVISION / 'complete.json').read_text())
                self.assertEqual(set(receipt['originals']), {str(index) + '/' + name for index in range(2) for name in MODULES})
                for index, directory in enumerate(roots):
                    self.assertIsNone(receipt['originals'][str(index) + '/' + APPEARANCE])
                    self.assertFalse((directory / APPEARANCE).exists())
                    self.assertEqual((directory / URLS).read_bytes(), b'old urls')
                    self.assertEqual((directory / SETTINGS).read_bytes(), b'old settings')
                    self.assertEqual((directory / PHP).read_bytes(), b'php')

    def appearance_host(self):
        fixture = load('test_publish')
        host = fixture.FakeHost()
        for root in host.file_state['api']:
            root.update({URLS: fixture.OLD, SETTINGS: fixture.OLD, HEALTH: fixture.OLD})
        host.original_files = copy.deepcopy(host.file_state)
        host.release.update(profile=PROFILE, api_sha256={name: fixture.NEW for name in MODULES},
            previous_api_sha256={APPEARANCE: None, URLS: fixture.OLD, SETTINGS: fixture.OLD},
            preserved_api_sha256={PHP: fixture.OLD, HEALTH: fixture.OLD})
        original = host.helper
        def helper(kind, mode):
            result = original(kind, mode)
            if kind == 'api':
                result['modules'] = len(MODULES)
            return result
        host.helper = helper
        return host, fixture

    def test_publication_and_partial_failure_use_new_paired_backup_and_preserve_business(self):
        publisher = load('publish')
        for failure in (None, 'backup', 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host, fixture = self.appearance_host()
                host.fail = failure
                before = copy.deepcopy(host.business)
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertEqual(host.business, before)
                self.assertIn('paired-backup', host.events)
                self.assertEqual(result['published'], failure is None, result)
                if failure is None:
                    self.assertLess(host.events.index('paired-backup'), host.events.index('api-publish'))
                    self.assertIn('api-verify-backup-receipt', host.events)
                    self.assertIn('ui-verify-backup-receipt', host.events)
                else:
                    self.assertTrue(result['rolled_back'], result)
                    self.assertEqual(host.file_state, host.original_files)
                    self.assertTrue(all(APPEARANCE not in root for root in host.file_state['api']))

    def test_both_runtime_api_trees_reject_unrelated_drift_and_existing_new_module_prestate(self):
        publisher = load('publish')
        host, _ = self.appearance_host()
        publisher.verify_previous_files(host.file_state, host.release)
        for index in range(2):
            invalid = copy.deepcopy(host.file_state)
            invalid['api'][index][APPEARANCE] = 'f' * 64
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.verify_previous_files(invalid, host.release)
        current = copy.deepcopy(host.file_state)
        current['ui_revisions'] = [REVISION] * 2
        current['api'] = [dict(root, **host.release['api_sha256']) for root in current['api']]
        current['ui'] = [dict(root, **host.release['ui_sha256'], **{'ui-release.json': host.release['ui_release_sha256']})
                         for root in current['ui']]
        publisher.verify_target_files(current, host.file_state, host.release)
        for index in range(2):
            for change in ('extra.py', 'apps/ops/models.py'):
                invalid = copy.deepcopy(current)
                invalid['api'][index][change] = 'f' * 64
                with self.subTest(root=index, change=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.verify_target_files(invalid, host.file_state, host.release)

    def test_incomplete_original_receipt_keeps_maintenance_closed(self):
        publisher = load('publish')
        for kind in ('api', 'ui'):
            host, fixture = self.appearance_host()
            host.fail = kind + '-incomplete-receipt'
            result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
            self.assertFalse(result['published'])
            self.assertFalse(result['rolled_back'])
            self.assertTrue(result['maintenance_closed'])
            self.assertFalse(host.ingress or host.health)


if __name__ == '__main__':
    unittest.main()
