"""Explicit SSL configuration classification: fixed existing API/UI files and reversible publication."""
import copy
import contextlib
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import shutil
import tempfile
import sys
import unittest
from unittest import mock

HERE = Path(__file__).parent
PROFILE = 'ssl-configuration-status'
MODULES = ('apps/monitor/ssl/service.py', 'apps/monitor/ssl/collectors.py', 'apps/monitor/ssl/jobs.py')
UI = {'spug_web/src/pages/ssl-monitor/' + name for name in ('index.js', 'index.module.less', 'helpers.js', 'CertificateDetail.js')}
PHP, HEALTH = 'apps/baota/operations/php_update.py', 'apps/setting/health.py'
REVISION, PREVIOUS = 'a' * 40, 'b' * 40


def load(name):
    spec = importlib.util.spec_from_file_location('ssl_visibility_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class SSLConfigurationReleaseTests(unittest.TestCase):
    def test_package_cli_expands_each_existing_tuple_profile_into_individual_copy_lines(self):
        package = load('package')
        for profile in (PROFILE,):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                stage = load('test_package').PackagingTests().profile_stage(root)
                repo = root / 'repo'
                shutil.copytree(stage / 'source/spug_api', repo / 'spug_api')
                shutil.copytree(stage / 'build', repo / 'spug_web/build')
                module = package.PROFILES[profile]
                modules = module if isinstance(module, tuple) else (module,)
                ui = (UI if profile == PROFILE else package.CONTINUOUS_UI if profile == 'continuous-inspections'
                      else {'spug_web/src/pages/ssl-monitor/index.js'} if profile == 'ssl-current-results' else set())
                names = ['spug_api/' + name for name in modules] + sorted(ui)
                for name in names:
                    target = repo / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'new module')
                for name in ('expiry/apply-expiry.py', 'upgrade-review/publish-ui.py',
                             'upgrade-review/fingerprint.py', 'inventory/release_safety.py'):
                    target = repo / 'deploy' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'# helper')
                def git(command, **kwargs):
                    args = command[1:]
                    if args == ['rev-parse', 'HEAD']: return REVISION.encode()
                    if args[:2] == ['rev-parse', '--verify']: return PREVIOUS.encode()
                    if args[0] == 'status': return b''
                    if args[0] == 'diff': return '\n'.join(names).encode()
                    if args[0] == 'ls-files': return '\0'.join(names + ['spug_api/' + PHP, 'spug_api/' + HEALTH, '']).encode()
                    if args[0] == 'show':
                        return b'preserved PHP' if args[1].endswith(PHP) else b'new health' if args[1].endswith(HEALTH) else b'old module'
                    self.fail('Unexpected Git command')
                with mock.patch.object(package, '__file__', str(repo / 'deploy/upgrade-review/package.py')), \
                        mock.patch.object(sys, 'argv', ['package.py', PREVIOUS, '--node', 'node', '--profile', profile]), \
                        mock.patch.object(package.subprocess, 'check_output', side_effect=git), \
                        mock.patch.object(package.subprocess, 'run'), mock.patch('builtins.print'):
                    package.main()
                lines = (root / ('upgrade-' + REVISION[:7]) / 'Dockerfile').read_text().splitlines()
                self.assertEqual([line for line in lines if line.startswith('COPY source/')],
                    ['COPY source/spug_api/' + name + ' /opt/spug/spug_api/' + name for name in modules])

    def stage(self, root):
        stage = load('test_package').PackagingTests().profile_stage(Path(root))
        for name in MODULES:
            target = stage / 'source/spug_api' / name
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'new ' + name.encode())
        return stage

    def manifest(self, stage):
        return load('package').write_manifests(stage, REVISION, PREVIOUS,
            {name: b'old ' + name.encode() for name in MODULES}, PROFILE,
            {PHP: b'preserved PHP', HEALTH: b'new health'})

    def test_exact_runtime_scope_rejects_models_extra_api_and_dependency_changes(self):
        package = load('package'); names = ['spug_api/' + n for n in MODULES] + sorted(UI)
        package.validate_runtime_changes(names + ['spug_api/apps/monitor/ssl/tests.py', 'spug_web/src/pages/ssl-monitor/index.test.js'], PROFILE)
        for bad in (names[:-1], names[1:], names + ['spug_api/apps/ops/models.py'],
                    names + ['spug_api/apps/monitor/ssl/renewal.py'], names + ['spug_web/src/routes.js'],
                    names + ['spug_web/package.json'], names + ['spug_api/apps/account/test_extra.py']):
            with self.subTest(bad=bad), self.assertRaises(ValueError): package.validate_runtime_changes(bad, PROFILE)

    def test_existing_hashes_image_overlay_and_prestate_are_strict(self):
        package, checker = load('package'), load('check_image')
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage(root); manifest = self.manifest(stage)
            self.assertEqual(set(manifest['api_sha256']), set(MODULES))
            self.assertTrue(all(manifest['previous_api_sha256'].values()))
            for invalid in (None, {}, {n: None for n in MODULES}):
                with self.assertRaises(ValueError):
                    package.write_manifests(stage, REVISION, PREVIOUS, invalid, PROFILE,
                                           {PHP: b'preserved PHP', HEALTH: b'new health'})
            runtime = Path(root) / 'runtime'
            shutil.copytree(stage / 'source/spug_api', runtime / 'spug_api')
            shutil.copytree(stage / 'build', runtime / 'spug_web/build')
            checker.verify(runtime, stage, REVISION)
            tampered = copy.deepcopy(manifest); tampered['previous_api_sha256'][MODULES[0]] = None
            (stage / 'release.json').write_text(json.dumps(tampered))
            with self.assertRaises(ValueError): checker.verify(runtime, stage, REVISION)

    def test_full_tree_rejects_unrelated_overlay_or_missing_old_module(self):
        prepare = load('prepare')
        old = {n: hashlib.sha256(('old ' + n).encode()).hexdigest() for n in MODULES}
        new = {n: hashlib.sha256(('new ' + n).encode()).hexdigest() for n in MODULES}
        baseline = dict(old, **{PHP: 'c' * 64, 'apps/monitor/ssl/renewal.py': 'd' * 64})
        candidate = dict(baseline, **new)
        self.assertTrue(prepare.ssl_configuration_api_digest(baseline, candidate, old, new))
        for modified in (dict(candidate, **{PHP: 'a' * 64}), {n: h for n, h in candidate.items() if n != PHP}):
            with self.assertRaises(ValueError): prepare.ssl_configuration_api_digest(baseline, modified, old, new)
        with self.assertRaises(ValueError):
            prepare.ssl_configuration_api_digest(baseline, candidate, dict(old, **{MODULES[0]: None}), new)

    def test_publisher_requires_full_tree_receipt_and_all_existing_module_hashes(self):
        publisher = load('publish'); receipts = load('test_appearance_release').AppearanceReleaseTests()
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage(root); manifest = self.manifest(stage)
            receipts.receipt(stage, manifest)
            self.assertEqual(set(publisher.load_stage(stage, REVISION, PREVIOUS)['api_sha256']), set(MODULES))
            for change in ({'baseline_image_id': None}, {'unchanged_api_tree_sha256': None}):
                receipts.receipt(stage, manifest, **change)
                with self.assertRaises(publisher.ReleaseFailure): publisher.load_stage(stage, REVISION, PREVIOUS)
            for name in MODULES:
                invalid = copy.deepcopy(manifest); invalid['previous_api_sha256'][name] = None
                receipts.receipt(stage, invalid)
                with self.assertRaises(publisher.ReleaseFailure): publisher.load_stage(stage, REVISION, PREVIOUS)

    def host(self):
        fixture = load('test_publish'); host = fixture.FakeHost()
        for api in host.file_state['api']:
            api.update({name: fixture.OLD for name in MODULES}); api[HEALTH] = fixture.OLD
        host.original_files = copy.deepcopy(host.file_state)
        host.release.update(profile=PROFILE, api_sha256={name: fixture.NEW for name in MODULES},
            previous_api_sha256={name: fixture.OLD for name in MODULES},
            preserved_api_sha256={PHP: fixture.OLD, HEALTH: fixture.OLD})
        original_helper = host.helper
        def helper(kind, mode):
            receipt = original_helper(kind, mode)
            if kind == 'api': receipt['modules'] = len(MODULES)
            return receipt
        host.helper = helper
        host.business['monitor_ssl_certificate'] = {'count': 11, 'digest': fixture.OLD}
        return host, fixture

    def test_success_and_partial_failure_preserve_business_and_restore_three_modules(self):
        publisher = load('publish')
        for failure in (None, 'backup', 'api-partial', 'ui-partial', 'http'):
            with self.subTest(failure=failure):
                host, fixture = self.host(); host.fail = failure; before = copy.deepcopy(host.business)
                result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                self.assertEqual(host.business, before)
                self.assertEqual(result['published'], failure is None, result)
                if failure is None:
                    self.assertLess(host.events.index('paired-backup'), host.events.index('api-publish'))
                    self.assertIn('api-verify-backup-receipt', host.events)
                else:
                    self.assertTrue(result['rolled_back'], result)
                    self.assertEqual(host.file_state, host.original_files)

    def test_any_missing_or_drifted_ssl_module_stops_before_maintenance(self):
        publisher = load('publish')
        for name in MODULES:
            for index in range(2):
                for invalid in (None, 'f' * 64):
                    host, fixture = self.host()
                    if invalid is None: del host.file_state['api'][index][name]
                    else: host.file_state['api'][index][name] = invalid
                    result = publisher.publish_release(host, fixture.REVISION, fixture.PREVIOUS)
                    self.assertFalse(result['published']); self.assertNotIn('close', host.events)
                    self.assertEqual(result['preflight_check'], 'runtime-baseline')

    def test_actual_three_module_overlay_and_second_root_failure_restore_every_file(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); stage = self.stage(root)
                manifest = self.manifest(stage)
                roots = [root / 'startup', root / 'live']
                for directory in roots:
                    for name in (*MODULES, PHP, HEALTH, 'apps/monitor/ssl/renewal.py', 'apps/monitor/ssl/models.py'):
                        target = directory / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b'original ' + name.encode())
                baseline = {str(path): path.read_bytes() for directory in roots for path in directory.rglob('*') if path.is_file()}
                source = (HERE.parent / 'expiry/apply-expiry.py').read_text()
                source = source.replace("Path('/tmp/spug-ui-' + revision[:7])", 'Path(' + repr(str(stage)) + ')')
                source = source.replace("[Path('/opt/spug/spug_api'), Path('/data/spug/spug_api')]",
                    repr(roots).replace('WindowsPath(', 'Path(').replace('PosixPath(', 'Path('))
                source = source.replace("Path('/data/spug-api-backups')", 'Path(' + repr(str(root / 'backups')) + ')')
                original_copy, failed = shutil.copyfile, []
                def copy_file(origin, target, *args, **kwargs):
                    if failure and not failed and Path(target) == roots[1] / 'apps/monitor/ssl/.jobs.py.release':
                        failed.append(True)
                        raise OSError('injected partial second-root failure')
                    return original_copy(origin, target, *args, **kwargs)
                def execute(mode):
                    with mock.patch.object(sys, 'argv', ['overlay', REVISION, mode]), contextlib.redirect_stdout(io.StringIO()):
                        exec(compile(source, '<isolated-overlay>', 'exec'), {'__name__': '__main__'})
                with mock.patch.object(shutil, 'copyfile', side_effect=copy_file):
                    if failure:
                        with self.assertRaises(OSError): execute('publish')
                        self.assertTrue(failed)
                    else:
                        execute('publish')
                        for directory in roots:
                            for name in MODULES:
                                self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), manifest['api_sha256'][name])
                        execute('restore')
                for path, original in baseline.items():
                    self.assertEqual(Path(path).read_bytes(), original)
                receipt = json.loads((root / 'backups' / REVISION / 'complete.json').read_text())
                self.assertEqual(set(receipt['originals']), {str(index) + '/' + name for index in range(2) for name in MODULES})
