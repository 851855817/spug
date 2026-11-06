"""Scheduled panel tasks are a fixed overlay with the existing release gates."""
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
PROFILE = 'scheduled-panel-tasks'
NEW_MODULE = 'apps/schedule/panel_tasks.py'
MODULES = (NEW_MODULE, 'apps/schedule/views.py', 'apps/schedule/urls.py',
           'apps/schedule/models.py', 'apps/schedule/executors.py', 'apps/schedule/scheduler.py',
           'apps/schedule/builtin.py', 'apps/baota/task_service.py', 'apps/baota/worker.py',
           'apps/baota/upload_store.py', 'apps/baota/upload_runner.py')
API_TESTS = {'spug_api/apps/schedule/test_panel_tasks.py', 'spug_api/apps/schedule/test_urls.py'}
UI = {'spug_web/src/pages/schedule/' + name for name in (
    'Form.js', 'Info.js', 'Record.js', 'Step1.js', 'Step2.js', 'Step3.js', 'Table.js',
    'index.js', 'store.js', 'PanelPlanSelector.js', 'PanelPlanSummary.js', 'PanelPreview.js', 'panelPlan.js')}
PHP, HEALTH = 'apps/baota/operations/php_update.py', 'apps/setting/health.py'
REVISION, PREVIOUS = 'a' * 40, 'b' * 40


def load(name):
    spec = importlib.util.spec_from_file_location('scheduled_panel_release_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScheduledPanelTasksReleaseTests(unittest.TestCase):
    def previous(self):
        return {name: None if name == NEW_MODULE else b'old ' + name.encode() for name in MODULES}

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
        return load('package').write_manifests(stage, REVISION, PREVIOUS, self.previous(), PROFILE,
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

    def test_scope_requires_exact_eleven_api_and_thirteen_ui_files_and_only_two_backend_tests(self):
        package = load('package')
        names = {'spug_api/' + name for name in MODULES} | UI
        package.validate_runtime_changes(sorted(names | API_TESTS | {
            'spug_web/src/pages/schedule/PanelPreview.test.js', 'docs/guide.md'}), PROFILE)
        for missing in names:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                package.validate_runtime_changes(sorted(names - {missing}), PROFILE)
        for extra in ('spug_api/apps/account/views.py', 'spug_api/apps/baota/models.py',
                      'spug_api/apps/baota/test_tasks.py', 'spug_api/apps/schedule/test_unreviewed.py',
                      'spug_api/apps/schedule/migrations/0002_panel_tasks.py',
                      'spug_api/apps/schedule/migrations/test_hidden.py', 'spug_api/spug/settings.py',
                      'spug_web/src/routes.js', 'spug_web/package.json', 'spug_web/package-lock.json'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                package.validate_runtime_changes(sorted(names | {extra}), PROFILE)
        for profile in ('panel-login', 'site-appearance', 'task-results-ui'):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                package.validate_runtime_changes(sorted(names), profile)

    def test_manifest_and_image_require_only_new_module_absent_and_preserve_php_health(self):
        package, checker = load('package'), load('check_image')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.assertEqual(set(release['api_sha256']), set(MODULES))
            self.assertEqual(release['previous_api_sha256'], {name:
                hashlib.sha256(content).hexdigest() if content is not None else None
                for name, content in self.previous().items()})
            self.assertEqual(set(release['preserved_api_sha256']), {PHP, HEALTH})
            invalid_baselines = [{}, dict(self.previous(), extra=None),
                                 dict(self.previous(), **{NEW_MODULE: b'existing'})]
            invalid_baselines += [dict(self.previous(), **{name: None}) for name in MODULES[1:]]
            for previous in invalid_baselines:
                with self.subTest(previous=previous), self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, previous, PROFILE,
                                            {PHP: b'php', HEALTH: b'health'})
            runtime = Path(folder) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, REVISION)
            invalid_releases = [dict(release, previous_api_sha256=dict(release['previous_api_sha256'],
                                  **{NEW_MODULE: 'f' * 64})), dict(release, preserved_api_sha256={}),
                                dict(release, api_sha256=dict(release['api_sha256'], **{PHP: 'f' * 64}))]
            invalid_releases += [dict(release, previous_api_sha256=dict(release['previous_api_sha256'],
                                    **{name: None})) for name in MODULES[1:]]
            for invalid in invalid_releases:
                (stage / 'release.json').write_text(json.dumps(invalid))
                with self.subTest(manifest=invalid), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, REVISION)
            (stage / 'release.json').write_text(json.dumps(release))
            for name in (PHP, HEALTH):
                path = runtime / 'spug_api' / name
                original = path.read_bytes()
                path.write_bytes(b'drift')
                with self.subTest(preserved=name), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, REVISION)
                path.write_bytes(original)

    def test_full_image_tree_checks_every_prestate_and_only_allows_exact_overlay(self):
        prepare = load('prepare')
        self.assertTrue(hasattr(prepare, 'scheduled_panel_tasks_api_digest'), 'fixed overlay needs complete image comparison')
        compare = prepare.scheduled_panel_tasks_api_digest
        previous = {name: None if name == NEW_MODULE else 'a' * 64 for name in MODULES}
        overlay = {name: 'b' * 64 for name in MODULES}
        baseline = {name: value for name, value in previous.items() if value is not None}
        baseline.update({PHP: 'c' * 64, HEALTH: 'd' * 64, 'config/local.ini': 'e' * 64})
        candidate = dict(baseline, **overlay)
        self.assertEqual(compare(baseline, candidate, previous, overlay), prepare.unchanged_api_digest(
            {name: value for name, value in baseline.items() if name not in MODULES},
            {name: value for name, value in candidate.items() if name not in MODULES}))
        for name in baseline:
            with self.subTest(baseline=name), self.assertRaises(ValueError):
                compare(dict(baseline, **{name: '0' * 64}), candidate, previous, overlay)
        for name in candidate:
            for invalid in (dict(candidate, **{name: '0' * 64}),
                            {key: value for key, value in candidate.items() if key != name}):
                with self.subTest(candidate=name), self.assertRaises(ValueError):
                    compare(baseline, invalid, previous, overlay)
        for old, new in ((dict(baseline, **{NEW_MODULE: 'a' * 64}), candidate),
                         (baseline, dict(candidate, **{'extra.py': 'a' * 64}))):
            with self.assertRaises(ValueError):
                compare(old, new, previous, overlay)
        for old, new in ((dict(previous, extra=None), overlay), (previous, dict(overlay, extra='a' * 64)),
                         (dict(previous, **{NEW_MODULE: 'a' * 64}), overlay),
                         (dict(previous, **{MODULES[1]: None}), overlay)):
            with self.assertRaises(ValueError):
                compare(baseline, candidate, old, new)

    def test_package_cli_reads_absent_prestate_and_copies_only_eleven_modules(self):
        package = load('package')
        for existing in (False, True):
            with self.subTest(new_module_exists=existing), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                stage = self.make_stage(root)
                repo = root / 'repo'
                shutil.copytree(stage / 'source/spug_api', repo / 'spug_api')
                shutil.copytree(stage / 'build', repo / 'spug_web/build')
                workflow_name = '.github/workflows/baota-checks.yml'
                workflow = repo / workflow_name
                workflow.parent.mkdir(parents=True)
                workflow.write_bytes(b'# archived CI contract\n')
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
                    if values[0] == 'ls-tree':
                        return b'spug_api/' + NEW_MODULE.encode() if existing else b''
                    if values[0] == 'diff':
                        return '\n'.join(['spug_api/' + name for name in MODULES] + sorted(UI)).encode()
                    if values[0] == 'ls-files':
                        tracked = ['spug_api/' + name for name in (*MODULES, PHP, HEALTH)] + [workflow_name]
                        return '\0'.join(name for name in tracked if any(
                            name == prefix or name.startswith(prefix + '/') for prefix in values[2:])).encode()
                    if values[0] == 'show':
                        return {**self.previous(), PHP: b'php', HEALTH: b'health'}[values[1].split(':spug_api/')[1]]
                    self.fail('unexpected Git command')
                with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                        mock.patch.object(sys, 'argv', ['package.py', PREVIOUS, '--node', 'node', '--profile', PROFILE]), \
                        mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                        mock.patch.object(package.subprocess, 'run'), mock.patch('builtins.print'):
                    if existing:
                        with self.assertRaises(ValueError):
                            package.main()
                    else:
                        package.main()
                if existing:
                    continue
                packaged = root / ('upgrade-' + REVISION[:7])
                self.assertEqual((packaged / 'source' / workflow_name).read_bytes(), workflow.read_bytes())
                with tarfile.open(packaged.with_suffix('.tar.gz')) as archive:
                    self.assertEqual(archive.extractfile(packaged.name + '/source/' + workflow_name).read(),
                                     workflow.read_bytes())
                release = json.loads((packaged / 'release.json').read_text())
                self.assertIsNone(release['previous_api_sha256'][NEW_MODULE])
                self.assertIn(['ls-tree', '--name-only', PREVIOUS, '--', 'spug_api/' + NEW_MODULE], calls)
                copies = [line for line in (packaged / 'Dockerfile').read_text().splitlines() if line.startswith('COPY source/')]
                self.assertEqual(copies, ['COPY source/spug_api/' + name + ' /opt/spug/spug_api/' + name for name in MODULES])

    def test_prepare_requires_full_tree_and_backend_regressions_before_receipt(self):
        for failure in (None, 'backend-drift', 'baseline-moved', 'schedule-test-failed'):
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
                    if 'apps.schedule.test_panel_tasks' in command and failure == 'schedule-test-failed':
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
                    self.assertTrue(any('apps.schedule.test_panel_tasks' in command and 'apps.schedule.test_urls' in command
                                        for command in commands), 'isolated scheduled task and URL tests must run')
                    load('publish').load_stage(receipt.parent, REVISION, PREVIOUS)

    def test_publisher_requires_overlay_prestate_preservation_and_full_image_receipt(self):
        publisher = load('publish')
        with tempfile.TemporaryDirectory() as folder:
            stage = self.make_stage(folder)
            release = self.manifest(stage)
            self.receipt(stage, release)
            loaded = publisher.load_stage(stage, REVISION, PREVIOUS)
            self.assertEqual(set(loaded['api_sha256']), set(MODULES))
            for change in ({'baseline_image_id': None}, {'baseline_image_id': 'sha256:' + 'c' * 64},
                           {'unchanged_api_tree_sha256': None}, {'linux_regression': False}):
                self.receipt(stage, release, **change)
                with self.subTest(receipt=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)
            changes = [{'previous_api_sha256': dict(release['previous_api_sha256'], **{NEW_MODULE: 'a' * 64})},
                       {'api_sha256': dict(release['api_sha256'], **{PHP: 'a' * 64})},
                       {'preserved_api_sha256': {}}, {'schema_changes': True}]
            changes += [{'previous_api_sha256': dict(release['previous_api_sha256'], **{name: None})}
                        for name in MODULES[1:]]
            for change in changes:
                self.receipt(stage, dict(release, **change))
                with self.subTest(manifest=change), self.assertRaises(publisher.ReleaseFailure):
                    publisher.load_stage(stage, REVISION, PREVIOUS)

    def test_real_overlay_receipt_restores_ten_modules_and_removes_new_file_from_both_roots(self):
        for failure in (False, True):
            with self.subTest(partial_failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                stage = self.make_stage(root)
                self.manifest(stage)
                roots = [root / 'startup', root / 'live']
                for directory in roots:
                    for name, content in {**self.previous(), PHP: b'php', HEALTH: b'health'}.items():
                        if content is not None:
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
                def copy_file(origin, target, *args, **kwargs):
                    if failure and not failed and Path(target) == roots[1] / 'apps/schedule/.views.py.release':
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
                        self.assertTrue(all((directory / NEW_MODULE).exists() for directory in roots))
                        execute('restore')
                receipt = json.loads((root / 'backups' / REVISION / 'complete.json').read_text())
                self.assertEqual(set(receipt['originals']), {str(index) + '/' + name for index in range(2) for name in MODULES})
                for index, directory in enumerate(roots):
                    self.assertIsNone(receipt['originals'][str(index) + '/' + NEW_MODULE])
                    for name, content in {**self.previous(), PHP: b'php', HEALTH: b'health'}.items():
                        path = directory / name
                        self.assertEqual(path.read_bytes() if path.exists() else None, content)

    def host(self):
        fixture = load('test_publish')
        host = fixture.FakeHost()
        for root in host.file_state['api']:
            root.update({name: fixture.OLD for name in (*MODULES[1:], HEALTH)})
        host.original_files = copy.deepcopy(host.file_state)
        host.release.update(profile=PROFILE, api_sha256={name: fixture.NEW for name in MODULES},
            previous_api_sha256={name: None if name == NEW_MODULE else fixture.OLD for name in MODULES},
            preserved_api_sha256={PHP: fixture.OLD, HEALTH: fixture.OLD})
        original = host.helper
        def helper(kind, mode):
            result = original(kind, mode)
            if kind == 'api':
                result['modules'] = len(MODULES)
            return result
        host.helper = helper
        return host, fixture

    def test_publication_partial_failure_and_fresh_paired_backup_preserve_business(self):
        publisher = load('publish')
        for failure in (None, 'backup', 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host, fixture = self.host()
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
                    self.assertTrue(all(NEW_MODULE not in root for root in host.file_state['api']))

    def test_stale_version_and_active_business_reject_before_maintenance_or_backups(self):
        publisher = load('publish')
        for failure in ('stale-version', 'active-business'):
            with self.subTest(failure=failure):
                host, fixture = self.host()
                if failure == 'stale-version':
                    host.file_state['ui_revisions'] = ['c' * 40] * 2
                else:
                    host.active = True
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertFalse(result['published'])
                self.assertTrue(host.health and host.ingress)
                self.assertNotIn('close', host.events)
                self.assertNotIn('paired-backup', host.events)

    def test_both_runtime_trees_reject_existing_new_module_and_unrelated_target_drift(self):
        publisher = load('publish')
        host, _ = self.host()
        publisher.verify_previous_files(host.file_state, host.release)
        for index in range(2):
            invalid = copy.deepcopy(host.file_state)
            invalid['api'][index][NEW_MODULE] = 'f' * 64
            with self.assertRaises(publisher.ReleaseFailure):
                publisher.verify_previous_files(invalid, host.release)
        current = copy.deepcopy(host.file_state)
        current['ui_revisions'] = [REVISION] * 2
        current['api'] = [dict(root, **host.release['api_sha256']) for root in current['api']]
        current['ui'] = [dict(root, **host.release['ui_sha256'], **{'ui-release.json': host.release['ui_release_sha256']})
                         for root in current['ui']]
        publisher.verify_target_files(current, host.file_state, host.release)
        for index in range(2):
            for name in ('extra.py', 'apps/ops/models.py', PHP, HEALTH):
                invalid = copy.deepcopy(current)
                invalid['api'][index][name] = 'f' * 64
                with self.subTest(root=index, drift=name), self.assertRaises(publisher.ReleaseFailure):
                    publisher.verify_target_files(invalid, host.file_state, host.release)

    def test_incomplete_receipts_and_new_activity_keep_maintenance_closed(self):
        publisher = load('publish')
        for failure in ('api-incomplete-receipt', 'ui-incomplete-receipt', 'active-after-open',
                        'business-after-open', 'schema', 'credentials', 'pid'):
            with self.subTest(failure=failure):
                host, fixture = self.host()
                host.fail = failure
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertFalse(result['published'])
                self.assertFalse(result['rolled_back'])
                self.assertTrue(result['maintenance_closed'])
                self.assertFalse(host.ingress or host.health)

    def test_ci_runs_schedule_tests_and_full_image_contains_exact_schedule_runtime_sources(self):
        repo = HERE.parents[1]
        workflow = (repo / '.github/workflows/baota-checks.yml').read_text()
        self.assertIn("'spug_api/apps/schedule/**'", workflow)
        selector = load_selector = importlib.util.spec_from_file_location(
            'scheduled_complete_ci', repo / 'deploy/account-resources/ci_tests.py')
        selector = importlib.util.module_from_spec(load_selector)
        load_selector.loader.exec_module(selector)
        self.assertIn('apps.schedule.test_panel_tasks', selector.discover(repo / 'spug_api'))
        for line in (line for line in workflow.splitlines() if 'manage.py test ' in line):
            self.assertIn('"${backend_test_modules[@]}"', line)
        dockerfile = (repo / 'deploy/baota/Dockerfile').read_text()
        self.assertIn('COPY spug_api/ /opt/spug/spug_api/', dockerfile)
        self.assertIn('cp -a spug_api "$baota_context/"', workflow)


if __name__ == '__main__':
    unittest.main()
