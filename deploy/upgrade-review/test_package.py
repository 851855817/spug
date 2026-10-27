"""Check release scope, receipts and archive boundaries without production access."""
import importlib.util
import ast
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).parent
PHP = 'apps/baota/operations/php_update.py'
HEALTH = 'apps/setting/health.py'


class PackagingTests(unittest.TestCase):
    def helper(self, filename):
        path = ROOT / filename
        self.assertTrue(path.exists(), 'Upgrade review release helper is not implemented')
        spec = importlib.util.spec_from_file_location('upgrade_' + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def profile_stage(self, root):
        stage = root / 'stage'
        for name, content in ((PHP, b'preserved PHP'), (HEALTH, b'new health')):
            path = stage / 'source/spug_api' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (stage / 'build').mkdir()
        (stage / 'build/index.html').write_bytes(b'index')
        (stage / 'build/asset-manifest.json').write_bytes(b'{}')
        for name in ('apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'):
            (stage / name).write_bytes(b'# helper')
        return stage

    def test_operations_ui_scope_accepts_only_the_fixed_ui_change_and_no_backend_runtime(self):
        package = self.helper('package.py')
        ui = 'spug_web/src/pages/exec/operations/RunDetail.js'
        package.validate_runtime_changes([ui, 'spug_api/apps/ops/expiry/tests.py',
                                          'spug_web/src/pages/exec/operations/index.test.js'], 'operations-ui')
        for names in ([], [ui, 'spug_api/' + PHP], [ui, 'spug_api/' + HEALTH],
                      [ui, 'spug_api/spug/settings.py'], [ui, 'spug_web/src/routes.js'],
                      [ui, 'spug_web/package.json'], ['spug_web/src/pages/baota/index.js']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, 'operations-ui')

    def test_operations_ui_manifests_have_no_overlay_and_preserve_both_fixed_modules(self):
        package = self.helper('package.py')
        baseline = {PHP: b'preserved PHP', HEALTH: b'new health'}
        with TemporaryDirectory() as folder:
            stage = self.profile_stage(Path(folder))
            manifest = package.write_manifests(stage, 'a' * 40, 'b' * 40, None,
                                               'operations-ui', preserved_content=baseline)
            self.assertEqual(manifest['api_sha256'], {})
            self.assertEqual(manifest['previous_api_sha256'], {})
            self.assertEqual(manifest['preserved_api_sha256'],
                             {name: hashlib.sha256(value).hexdigest() for name, value in baseline.items()})
            self.assertEqual(json.loads((stage / 'api-release.json').read_text())['sha256'], {})
            for content in (None, {}, {PHP: b'preserved PHP'}, dict(baseline, extra=b'wrong'),
                            dict(baseline, **{HEALTH: b'drift'})):
                with self.subTest(content=content), self.assertRaises(ValueError):
                    package.write_manifests(stage, 'a' * 40, 'b' * 40, None,
                                            'operations-ui', preserved_content=content)

    def test_host_profile_accepts_only_the_health_runtime_change(self):
        package = self.helper('package.py')
        package.validate_runtime_changes(['spug_api/' + HEALTH, 'spug_api/apps/setting/test_health.py'],
                                         profile='host-report')
        for names in (['spug_api/' + PHP], ['spug_api/' + PHP, 'spug_api/' + HEALTH],
                      ['spug_api/' + HEALTH, 'spug_api/apps/ops/models.py'], []):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, profile='host-report')
        for profile in ('unknown', '', None, [], '../apps/setting/health.py'):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                package.validate_runtime_changes(['spug_api/' + HEALTH], profile=profile)

    def test_host_manifest_preserves_baseline_php_separately_from_health_overlay(self):
        package = self.helper('package.py')
        with TemporaryDirectory() as folder:
            stage = self.profile_stage(Path(folder))
            manifest = package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old health',
                                               profile='host-report', preserved_content=b'preserved PHP')
            self.assertEqual(manifest['profile'], 'host-report')
            self.assertEqual(manifest['api_sha256'], {HEALTH: hashlib.sha256(b'new health').hexdigest()})
            self.assertEqual(manifest['previous_api_sha256'], {HEALTH: hashlib.sha256(b'old health').hexdigest()})
            self.assertEqual(manifest['preserved_api_sha256'], {PHP: hashlib.sha256(b'preserved PHP').hexdigest()})
            self.assertEqual(json.loads((stage / 'api-release.json').read_text())['sha256'],
                             manifest['api_sha256'])

    def test_host_package_refuses_missing_or_drifted_baseline_php(self):
        package = self.helper('package.py')
        with TemporaryDirectory() as folder:
            stage = self.profile_stage(Path(folder))
            for baseline in (None, b'different PHP'):
                with self.subTest(baseline=baseline), self.assertRaises(ValueError):
                    package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old health',
                                            profile='host-report', preserved_content=baseline)
            (stage / 'source/spug_api' / PHP).unlink()
            with self.assertRaises(ValueError):
                package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old health',
                                        profile='host-report', preserved_content=b'preserved PHP')

    def test_host_image_requires_preserved_php_and_only_the_fixed_health_overlay(self):
        checker = self.helper('check_image.py')
        with TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.profile_stage(root)
            runtime = root / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            release = {'revision': 'a' * 40, 'schema_changes': False, 'profile': 'host-report',
                       'api_sha256': {HEALTH: hashlib.sha256(b'new health').hexdigest()},
                       'previous_api_sha256': {HEALTH: hashlib.sha256(b'old health').hexdigest()},
                       'preserved_api_sha256': {PHP: hashlib.sha256(b'preserved PHP').hexdigest()}}
            ui = {'revision': 'a' * 40, 'sha256': {name: hashlib.sha256((stage / 'build' / name).read_bytes()).hexdigest()
                  for name in ('index.html', 'asset-manifest.json')}}
            (stage / 'release.json').write_text(json.dumps(release))
            (stage / 'build/ui-release.json').write_text(json.dumps(ui))
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            try:
                checker.verify(runtime, stage, 'a' * 40)
            except ValueError as error:
                self.fail('The verified host-report image must be accepted: ' + str(error))
            for name in (PHP, HEALTH):
                with self.subTest(name=name):
                    path = runtime / 'spug_api' / name
                    content = path.read_bytes(); path.write_bytes(b'regressed image module')
                    with self.assertRaises(ValueError):
                        checker.verify(runtime, stage, 'a' * 40)
                    path.write_bytes(content)
            for change in ({'profile': 'unknown'}, {'profile': ''}, {'profile': None},
                           {'api_sha256': {HEALTH: release['api_sha256'][HEALTH], PHP: release['preserved_api_sha256'][PHP]}},
                           {'previous_api_sha256': {PHP: release['preserved_api_sha256'][PHP]}},
                           {'preserved_api_sha256': {}}, {'preserved_api_sha256': None},
                           {'preserved_api_sha256': {PHP: 'invalid'}},
                           {'preserved_api_sha256': {PHP: release['preserved_api_sha256'][PHP], HEALTH: 'c' * 64}}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    (stage / 'release.json').write_text(json.dumps(dict(release, **change)))
                    checker.verify(runtime, stage, 'a' * 40)
            missing = dict(release); missing.pop('preserved_api_sha256')
            (stage / 'release.json').write_text(json.dumps(missing))
            with self.assertRaises(ValueError):
                checker.verify(runtime, stage, 'a' * 40)

    def test_package_cli_builds_the_selected_fixed_overlay_and_reads_the_php_baseline(self):
        package = self.helper('package.py')
        for profile, api_name in (('host-report', HEALTH), ('php-update', PHP), ('operations-ui', None), ('task-results-ui', None), ('workbench-ui', None), ('branding-ui', None),
                                  ('inventory-filters', 'apps/baota/inventory/service.py')):
            with self.subTest(profile=profile), TemporaryDirectory() as folder:
                root = Path(folder)
                stage = self.profile_stage(root)
                repo = root / 'repo'
                shutil.copytree(stage / 'source/spug_api', repo / 'spug_api')
                if profile == 'inventory-filters':
                    target = repo / 'spug_api' / api_name
                    target.parent.mkdir(parents=True); target.write_bytes(b'new listing')
                shutil.copytree(stage / 'build', repo / 'spug_web/build')
                for name in ('expiry/apply-expiry.py', 'upgrade-review/publish-ui.py',
                             'upgrade-review/fingerprint.py', 'inventory/release_safety.py'):
                    path = repo / 'deploy' / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'# helper')
                calls = []
                def git(command, **kwargs):
                    calls.append(command)
                    values = command[1:]
                    if values == ['rev-parse', 'HEAD']:
                        return ('a' * 40).encode()
                    if values[:2] == ['rev-parse', '--verify']:
                        return ('b' * 40).encode()
                    if values[0] == 'status':
                        return b''
                    if values[0] == 'diff':
                        if profile in ('workbench-ui', 'branding-ui'):
                            return '\n'.join(sorted(package.WORKBENCH_UI if profile == 'workbench-ui' else package.BRANDING_UI)).encode()
                        if profile == 'inventory-filters':
                            return ('spug_api/' + api_name + '\nspug_web/src/pages/panel-inventory/index.js').encode()
                        return ('spug_api/' + api_name if api_name else '\n'.join(sorted(package.TASK_RESULTS_UI)) if profile == 'task-results-ui' else package.UI_FILE).encode()
                    if values[0] == 'ls-files':
                        return ('spug_api/' + PHP + '\nspug_api/' + HEALTH + ('\nspug_api/' + api_name if profile == 'inventory-filters' else '')).encode()
                    if values[0] == 'show':
                        return b'preserved PHP' if values[1].endswith(PHP) else b'new health' if values[1].endswith(HEALTH) and profile in ('operations-ui', 'task-results-ui', 'inventory-filters', 'workbench-ui', 'branding-ui') else b'old health'
                    self.fail('Unexpected packaging command')
                arguments = ['package.py', 'b' * 40, '--node', 'node']
                if profile != 'php-update':
                    arguments += ['--profile', profile]
                with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                        mock.patch.object(sys, 'argv', arguments), \
                        mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                        mock.patch.object(package.subprocess, 'run') as build, mock.patch('builtins.print'):
                    package.main()
                    self.assertEqual(build.call_args.kwargs['env']['REACT_APP_RELEASE_REVISION'], 'a' * 40)
                packaged = root / ('upgrade-' + 'a' * 7)
                manifest = json.loads((packaged / 'release.json').read_text())
                self.assertEqual(manifest['profile'], profile)
                self.assertEqual(set(manifest['api_sha256']), {api_name} if api_name else set())
                preserved = {PHP: hashlib.sha256(b'preserved PHP').hexdigest()} if profile != 'php-update' else {}
                if profile in ('operations-ui', 'task-results-ui', 'inventory-filters', 'workbench-ui', 'branding-ui'):
                    preserved[HEALTH] = hashlib.sha256(b'new health').hexdigest()
                self.assertEqual(manifest['preserved_api_sha256'], preserved)
                copies = [line for line in (packaged / 'Dockerfile').read_text().splitlines()
                          if line.startswith('COPY source/')]
                self.assertEqual(copies, ['COPY source/spug_api/' + api_name + ' /opt/spug/spug_api/' + api_name] if api_name else [])
                if api_name:
                    self.assertIn(['git', 'show', 'b' * 40 + ':spug_api/' + api_name], calls)
                if profile != 'php-update':
                    self.assertIn(['git', 'show', 'b' * 40 + ':spug_api/' + PHP], calls)
                if profile in ('operations-ui', 'task-results-ui', 'inventory-filters', 'workbench-ui', 'branding-ui'):
                    self.assertIn(['git', 'show', 'b' * 40 + ':spug_api/' + HEALTH], calls)

    def test_image_api_comparison_rejects_changes_additions_deletions_and_invalid_hashes(self):
        prepare = self.helper('prepare.py')
        baseline = {'runtime.py': 'a' * 64, 'config/custom.ini': 'b' * 64}
        digest = prepare.unchanged_api_digest(baseline, dict(reversed(list(baseline.items()))))
        self.assertRegex(digest, '^[0-9a-f]{64}$')
        for changed in ({}, {'runtime.py': 'a' * 64}, dict(baseline, extra='c' * 64),
                        dict(baseline, **{'runtime.py': 'c' * 64}), dict(baseline, **{'runtime.py': 'invalid'})):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                prepare.unchanged_api_digest(baseline, changed)

    def test_image_api_probe_uses_immutable_image_and_isolation_without_a_data_mount(self):
        prepare = self.helper('prepare.py')
        image = 'sha256:' + 'c' * 64
        with mock.patch.object(prepare.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 0, stdout='{"runtime.py":"' + 'a' * 64 + '"}')) as run:
            self.assertEqual(prepare.image_api_snapshot(image, '# fixed probe'), {'runtime.py': 'a' * 64})
        command = run.call_args[0][0]
        self.assertIn('--read-only', command)
        self.assertEqual(command[command.index('--network') + 1], 'none')
        self.assertNotIn('-v', command)
        self.assertNotIn('--mount', command)
        self.assertIn(image, command)
        self.assertEqual(run.call_args.kwargs['input'], '# fixed probe')
        self.assertEqual(json.loads(command[-1]), {'mode': 'image-api', 'root': '/opt/spug/spug_api'})

    def test_operations_ui_image_manifest_accepts_only_empty_overlay_and_both_preserved_modules(self):
        package = self.helper('package.py')
        checker = self.helper('check_image.py')
        with TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.profile_stage(root)
            manifest = package.write_manifests(stage, 'a' * 40, 'b' * 40, None, 'operations-ui',
                                               {PHP: b'preserved PHP', HEALTH: b'new health'})
            runtime = root / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, 'a' * 40)
            for name in (PHP, HEALTH):
                path = runtime / 'spug_api' / name
                original = path.read_bytes()
                path.write_bytes(b'drift')
                with self.subTest(name=name), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, 'a' * 40)
                path.write_bytes(original)
            for change in ({'api_sha256': {PHP: 'c' * 64}}, {'previous_api_sha256': {HEALTH: 'c' * 64}},
                           {'preserved_api_sha256': {PHP: manifest['preserved_api_sha256'][PHP]}}):
                (stage / 'release.json').write_text(json.dumps(dict(manifest, **change)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    checker.verify(runtime, stage, 'a' * 40)

    def test_operations_ui_prepare_writes_receipt_only_after_full_api_and_baseline_identity_checks(self):
        for profile, failure in ((p, f) for p in ('operations-ui', 'task-results-ui', 'workbench-ui')
                                 for f in (None, 'backend-drift', 'baseline-moved')):
            with self.subTest(profile=profile, failure=failure), TemporaryDirectory() as folder:
                prepare = self.helper('prepare.py')
                root = Path(folder)
                stage = self.profile_stage(root / 'fixture')
                source_probe = stage / 'source/deploy/upgrade-review/publish.py'
                source_probe.parent.mkdir(parents=True)
                shutil.copyfile(ROOT / 'publish.py', source_probe)
                (stage / 'source/spug_api/apps/baota').mkdir(exist_ok=True)
                revision, previous = 'a' * 40, 'b' * 40
                new_id, old_id = 'sha256:' + 'c' * 64, 'sha256:' + 'd' * 64
                package = self.helper('package.py')
                package.write_manifests(stage, revision, previous, None, profile,
                                        {PHP: b'preserved PHP', HEALTH: b'new health'})
                archive = root / ('upgrade-' + revision[:7] + '.tar.gz')
                with tarfile.open(archive, 'w:gz') as output:
                    output.add(stage, arcname='upgrade-' + revision[:7])
                baseline_reads = []; probed = []
                def inspect(command, **kwargs):
                    baseline = command[-1] == 'spug-baota:' + previous[:7]
                    if baseline:
                        baseline_reads.append(command)
                    identity = old_id if baseline else new_id
                    if baseline and len(baseline_reads) == 2 and failure == 'baseline-moved':
                        identity = 'sha256:' + 'e' * 64
                    return json.dumps([{'Id': identity, 'Config': {'Labels': {
                        'org.opencontainers.image.revision': previous if baseline else revision}}}])
                def run(command, **kwargs):
                    result = ''
                    if command[-2] == '-':
                        probed.append(command[-3])
                        snapshot = {'runtime.py': 'f' * 64, 'config/custom.ini': 'a' * 64}
                        if command[-3] == new_id and failure == 'backend-drift':
                            snapshot['extra.py'] = 'b' * 64
                        result = json.dumps(snapshot)
                    return subprocess.CompletedProcess(command, 0, stdout=result)
                with mock.patch.object(prepare, 'Path', side_effect=lambda value: root if value == '/opt/spug' else Path(value)), \
                        mock.patch.object(prepare.subprocess, 'check_output', side_effect=inspect), \
                        mock.patch.object(prepare.subprocess, 'run', side_effect=run), \
                        mock.patch.object(prepare.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 3 * 1024**3})()), \
                        mock.patch.object(sys, 'argv', ['prepare.py', revision, hashlib.sha256(archive.read_bytes()).hexdigest()]), \
                        mock.patch('builtins.print'):
                    if failure:
                        with self.assertRaises(ValueError):
                            prepare.main()
                    else:
                        prepare.main()
                receipt = root / ('upgrade-' + revision[:7]) / 'isolated-checks.json'
                self.assertEqual(receipt.exists(), failure is None)
                self.assertFalse((receipt.parent / 'source/deploy/upgrade-review/__pycache__').exists(),
                                 'Preparation must not add unarchived bytecode to the immutable stage')
                self.assertEqual(probed, [old_id, new_id])
                if failure is None:
                    saved = json.loads(receipt.read_text())
                    self.assertEqual(saved['baseline_image_id'], old_id)
                    self.assertRegex(saved['unchanged_api_tree_sha256'], '^[0-9a-f]{64}$')
                    self.assertEqual(len(baseline_reads), 2)
                    publisher = self.helper('publish.py')
                    verified = publisher.load_stage(receipt.parent, revision, previous)
                    self.assertEqual(verified['prepared_baseline_image_id'], old_id)
                    self.assertEqual(verified['api_sha256'], {})

    def test_scope_refuses_model_settings_or_other_runtime_changes(self):
        package = self.helper('package.py')
        api = 'spug_api/apps/baota/operations/php_update.py'
        package.validate_runtime_changes([api, 'spug_api/apps/baota/test_php_update.py', 'docs/guide.md'])
        for name in ('spug_api/apps/ops/models.py', 'spug_api/spug/settings.py',
                     'spug_api/apps/schedule/scheduler.py', 'spug_api/apps/baota/operations/software.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.validate_runtime_changes([api, name])

    def test_receipt_contains_only_the_reviewed_api_file_and_all_ui_assets(self):
        package = self.helper('package.py')
        with TemporaryDirectory() as folder:
            stage = Path(folder)
            api = stage / 'source/spug_api/apps/baota/operations/php_update.py'
            api.parent.mkdir(parents=True); api.write_text('new runtime')
            build = stage / 'build'; build.mkdir()
            (build / 'index.html').write_text('new index')
            (build / 'asset-manifest.json').write_text('{}')
            for name in ('apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'):
                (stage / name).write_text('# checked helper ' + name)
            manifest = package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old runtime')
            self.assertEqual(manifest['previous_revision'], 'b' * 40)
            self.assertEqual(set(manifest['api_sha256']), {'apps/baota/operations/php_update.py'})
            self.assertNotEqual(manifest['api_sha256'], manifest['previous_api_sha256'])
            self.assertEqual(json.loads((stage / 'api-release.json').read_text())['sha256'], manifest['api_sha256'])
            ui = json.loads((build / 'ui-release.json').read_text())
            self.assertEqual(set(ui['sha256']), {'index.html', 'asset-manifest.json'})
            self.assertNotIn('ui-release.json', ui['sha256'])
            self.assertEqual(set(manifest['helper_sha256']), {'apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'})

    def test_unsafe_archive_is_rejected_before_extracting_any_member(self):
        prepare = self.helper('prepare.py')
        for name in ('../outside', '/absolute', 'upgrade-aaaaaaa/../../outside',
                     'wrong-stage/file', 'upgrade-aaaaaaa/back\\slash'):
            with self.subTest(name=name), TemporaryDirectory() as folder:
                root = Path(folder)
                archive = root / 'input.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    data = b'test'
                    good = tarfile.TarInfo('upgrade-aaaaaaa/good'); good.size = len(data)
                    output.addfile(good, io.BytesIO(data))
                    bad = tarfile.TarInfo(name); bad.size = len(data)
                    output.addfile(bad, io.BytesIO(data))
                with self.assertRaises(ValueError):
                    prepare.safe_extract(archive, root, 'upgrade-aaaaaaa')
                self.assertFalse((root / 'upgrade-aaaaaaa').exists())

    def test_archive_links_are_rejected(self):
        prepare = self.helper('prepare.py')
        with TemporaryDirectory() as folder:
            root = Path(folder); archive = root / 'input.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                member = tarfile.TarInfo('upgrade-aaaaaaa/link')
                member.type = tarfile.SYMTYPE; member.linkname = '/etc'
                output.addfile(member)
            with self.assertRaises(ValueError):
                prepare.safe_extract(archive, root, 'upgrade-aaaaaaa')

    def test_built_image_checks_actual_runtime_and_each_static_asset(self):
        checker = self.helper('check_image.py')
        package = self.helper('package.py')
        with TemporaryDirectory() as folder:
            stage = Path(folder) / 'stage'; stage.mkdir()
            runtime = Path(folder) / 'runtime'; runtime.mkdir()
            api_name = 'apps/baota/operations/php_update.py'
            for base in (stage / 'source/spug_api', runtime / 'spug_api'):
                path = base / api_name; path.parent.mkdir(parents=True)
                path.write_text('new runtime')
            for base in (stage / 'build', runtime / 'spug_web/build'):
                base.mkdir(parents=True)
                (base / 'index.html').write_text('new index')
                (base / 'asset-manifest.json').write_text('{}')
                (base / 'new.js').write_text('new asset')
            for name in package.HELPERS:
                (stage / name).write_text('# helper')
            package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old runtime')
            ui_receipt = (stage / 'build/ui-release.json').read_bytes()
            (runtime / 'spug_web/build/ui-release.json').write_bytes(ui_receipt)
            checker.verify(runtime, stage, 'a' * 40)
            for path in (runtime / 'spug_api' / api_name, runtime / 'spug_web/build/new.js',
                         runtime / 'spug_web/build/ui-release.json'):
                with self.subTest(path=path):
                    content = path.read_bytes(); path.write_bytes(b'wrong image content')
                    with self.assertRaises(ValueError):
                        checker.verify(runtime, stage, 'a' * 40)
                    path.write_bytes(content)

    def test_preservation_covers_all_business_models_and_only_excludes_monitor_cache(self):
        from types import SimpleNamespace
        def model(label, name):
            return type(name, (), {'_meta': SimpleNamespace(app_label=label)})
        candidates = [model(label, name) for label, name in (
            ('baota', 'Panel'), ('ops', 'ExpiryMetadata'), ('credential', 'Credential'),
            ('config', 'Config'), ('notify', 'Notify'), ('alarm', 'Alarm'), ('home', 'Notice'),
            ('app', 'Deploy'), ('account', 'User_roles'), ('baota', 'ResourceMonitorSnapshot'),
            ('baota', 'ResourceMonitorLease'), ('other', 'ResourceMonitorSnapshot'))]
        class Registry:
            def get_models(self, include_auto_created=False):
                self.included_relations = include_auto_created
                return candidates
        registry = Registry(); scope = {'apps': registry}
        tree = ast.parse((ROOT / 'fingerprint.py').read_text())
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id in ('labels', 'models')]
        exec(compile(ast.Module(body=assignments, type_ignores=[]), '<model-selection>', 'exec'), scope)
        self.assertTrue(registry.included_relations)
        self.assertEqual(scope['models'], candidates[:9] + candidates[-1:])

    def test_isolated_checks_bind_the_exact_image_and_full_revision(self):
        prepare = self.helper('prepare.py')
        image_id = 'sha256:' + 'c' * 64
        image = {'Id': image_id, 'Config': {'Labels': {'org.opencontainers.image.revision': 'a' * 40}}}
        self.assertEqual(prepare.image_identity(image, 'a' * 40), image_id)
        for wrong in ('sha256:short', 'spug-baota:aaaaaaa', ''):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                prepare.image_identity(dict(image, Id=wrong), 'a' * 40)
        with self.assertRaises(ValueError):
            prepare.image_identity(image, 'b' * 40)

    def test_schema_fingerprint_includes_cache_migrations_and_unmodeled_tables(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        descriptions = {'baota_monitor_snapshots': ['cached-column'],
                        'django_migrations': ['applied-column'], 'unmodeled_table': ['column']}
        introspection = SimpleNamespace(
            table_names=lambda: list(descriptions),
            get_table_description=lambda cursor, name: list(descriptions[name]),
            get_constraints=lambda cursor, name: {'pk': {'primary_key': True}})
        connection = SimpleNamespace(introspection=introspection, cursor=lambda: nullcontext(None))
        tree = ast.parse((ROOT / 'fingerprint.py').read_text())
        methods = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'read_schema']
        scope = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<schema-probe>', 'exec'), scope)
        self.assertIn('read_schema', scope, 'All database tables need a separate schema probe')
        original = scope['read_schema'](connection)
        self.assertEqual(set(original), set(descriptions))
        for name in descriptions:
            descriptions[name].append('changed-column')
            self.assertNotEqual(scope['read_schema'](connection), original)
            descriptions[name].pop()
        descriptions['new_table'] = ['new-column']
        self.assertNotEqual(scope['read_schema'](connection), original)

    def test_user_session_ttl_refresh_does_not_hide_identity_or_revocation_changes(self):
        from types import SimpleNamespace
        tree = ast.parse((ROOT / 'fingerprint.py').read_text())
        methods = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'normalized_rows']
        scope = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<session-normalization>', 'exec'), scope)
        self.assertIn('normalized_rows', scope)
        user = type('User', (), {'_meta': SimpleNamespace(app_label='account')})
        before = [{'id': 1, 'token_expired': 200, 'access_token': 'a' * 32, 'is_supper': False, 'is_active': True}]
        normalize = scope['normalized_rows']
        original = normalize(user, before, 100)
        self.assertEqual(normalize(user, [dict(before[0], token_expired=300)], 100), original)
        for change in ({'token_expired': 0}, {'token_expired': 99}, {'access_token': 'new'}, {'is_supper': True}):
            self.assertNotEqual(normalize(user, [dict(before[0], **change)], 100), original)
        self.assertEqual(before[0]['token_expired'], 200)
        other = type('Other', (), {'_meta': SimpleNamespace(app_label='account')})
        self.assertEqual(normalize(other, before, 100), before)
        for change in ({'is_active': False}, {'access_token': ''}, {'access_token': 'bad'}):
            row = dict(before[0], **change)
            self.assertNotEqual(normalize(user, [row], 100), normalize(user, [dict(row, token_expired=300)], 100))


if __name__ == '__main__':
    unittest.main()
