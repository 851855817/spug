import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

from contract import HELPER_SOURCES, MODEL_NAMES, derived_release, dockerfiles
from manifest import API_FILES
from schema import SSL_TABLES

spec = importlib.util.spec_from_file_location('ssl_host_tested', Path(__file__).with_name('host.py'))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class PreparedBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.revision, self.previous = 'a' * 40, 'b' * 40
        original = self.root / 'ssl-aaaaaaa'
        original.mkdir()
        for name in API_FILES:
            target = original / 'source/spug_api' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('# reviewed source\n')
        for name in HELPER_SOURCES.values():
            target = original / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('# reviewed helper\n')
        build = original / 'build'
        build.mkdir()
        (build / 'index.html').write_text('<html>fixture</html>')
        (build / 'asset-manifest.json').write_text('{}')
        ui = host.tree(build)
        (build / 'ui-release.json').write_text(json.dumps({'revision': self.revision, 'sha256': ui}))
        release = {'revision': self.revision, 'previous_revision': self.previous, 'schema_changes': True,
            'api_sha256': {name: host.digest(original / 'source/spug_api' / name) for name in API_FILES},
            'previous_api_sha256': {name: None for name in API_FILES},
            'ui_sha256': ui, 'ui_release_sha256': host.digest(build / 'ui-release.json')}
        (original / 'release.json').write_text(json.dumps(release))
        archive = original.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as output: output.add(original, arcname=original.name)
        prepared = self.root / 'ssl-prepared-aaaaaaa'
        shutil.copytree(original, prepared)
        migration_name = '0002_ssl_monitor'
        migration = 'from django.db import migrations, models\nclass Migration(migrations.Migration):\n dependencies=[]\n operations=['
        migration += ','.join("migrations.CreateModel(name=" + repr(name) + ",fields=[])" for name in sorted(MODEL_NAMES)) + ']\n'
        path = prepared / 'source/spug_api/apps/monitor/migrations/0002_ssl_monitor.py'
        path.parent.mkdir(parents=True)
        path.write_text(migration)
        derived = derived_release(release, migration_name, host.digest(path))
        (prepared / 'release.json').write_text(json.dumps(derived))
        (prepared / 'api-release.json').write_text(json.dumps({'revision': self.revision,
            'migration': migration_name, 'sha256': derived['api_sha256']}))
        for name, source in HELPER_SOURCES.items(): shutil.copy2(original / source, prepared / name)
        candidate, rollback = dockerfiles(self.previous, migration_name)
        (prepared / 'Dockerfile').write_text(candidate)
        (prepared / 'rollback.Dockerfile').write_text(rollback)
        checks = {'revision': self.revision, 'previous_revision': self.previous, 'schema_changes': True,
            'archive_sha256': host.digest(archive), 'migration': migration_name,
            'new_schema': {name: 'c' * 64 for name in SSL_TABLES},
            'original_files': host.tree(original), 'prepared_files': host.tree(prepared)}
        for name in ('linux_regression', 'mariadb_regression', 'migration_rehearsal', 'rollback_rehearsal',
                     'release_failure_tests', 'image_tree_exact', 'rollback_image_tree_exact'): checks[name] = True
        self.receipt = original / 'isolated-checks.json'
        self.receipt.write_text(json.dumps(checks))
        self.prepared = prepared

    def verify(self): return host.load_stage(self.root, self.revision, self.previous)

    def rehash_prepared_receipt(self):
        checks = json.loads(self.receipt.read_text())
        checks['prepared_files'] = host.tree(self.prepared)
        self.receipt.write_text(json.dumps(checks))

    def test_exact_derivation_loads_without_any_host_access(self):
        _stage, release, _checks = self.verify()
        self.assertTrue(release['schema_changes'])
        self.assertEqual(release['migration'], '0002_ssl_monitor')

    def test_arbitrary_prepared_injection_rejected_even_with_rehashed_receipt(self):
        (self.prepared / 'source/spug_api/arbitrary.py').write_text('SECRET')
        self.rehash_prepared_receipt()
        with self.assertRaises(host.base.ReleaseFailure): self.verify()

    def test_modified_prepared_source_rejected_even_with_rehashed_receipt(self):
        (self.prepared / 'source/spug_api/apps/baota/worker.py').write_text('unreviewed')
        self.rehash_prepared_receipt()
        with self.assertRaises(host.base.ReleaseFailure): self.verify()

    def test_modified_helper_and_rehashed_receipt_rejected(self):
        (self.prepared / 'apply-expiry.py').write_text('unreviewed')
        self.rehash_prepared_receipt()
        with self.assertRaises(host.base.ReleaseFailure): self.verify()

    def test_missing_migration_or_ddl_guard_rejected(self):
        checks = json.loads(self.receipt.read_text())
        checks['migration_rehearsal'] = False
        self.receipt.write_text(json.dumps(checks))
        with self.assertRaises(host.base.ReleaseFailure): self.verify()


class AdapterVerificationTests(unittest.TestCase):
    def fixture(self):
        adapter = host.SSLHost('a' * 40, 'b' * 40, root=Path('unused-test-root'))
        adapter.release = {'migration': '0002_ssl_monitor',
            'api_sha256': {name: 'c' * 64 for name in (*API_FILES, 'apps/monitor/migrations/0002_ssl_monitor.py')},
            'ui_sha256': {'new.js': 'd' * 64}}
        baseline = {'fingerprint': {'old': True}, 'identity': {'pid': '1'},
            'compose': 'image: spug-baota:bbbbbbb\n', 'files': {
                'api': [{'old.py': 'e' * 64, 'apps/monitor/models.py': 'f' * 64}] * 2,
                'ui': [{'index.html': '0' * 64}] * 2, 'ui_revisions': ['b' * 40] * 2}}
        adapter.fingerprint = lambda: {'new': True}
        adapter.identity = lambda: baseline['identity']
        return adapter, baseline

    def test_compatible_restore_preserves_new_model_registry_and_migration_only(self):
        adapter, baseline = self.fixture()
        keep = (*host.COMPAT_FILES, 'apps/monitor/migrations/0002_ssl_monitor.py')
        runtime = dict(baseline['files'], api=[dict(row, **{name: 'c' * 64 for name in keep})
                                            for row in baseline['files']['api']])
        adapter.runtime_files = lambda: runtime
        adapter.compose_text = lambda: 'image: spug-baota:ssl-rollback-aaaaaaa\n'
        adapter.verify_restored(baseline, {'new': True}, compatibility=True)
        runtime['api'][0]['apps/monitor/ssl/jobs.py'] = 'c' * 64
        with self.assertRaises(host.base.ReleaseFailure):
            adapter.verify_restored(baseline, {'new': True}, compatibility=True)

    def test_post_ingress_window_does_not_make_http_request(self):
        adapter, baseline = self.fixture()
        adapter.fingerprint = lambda: {'old': True}
        adapter.verify_http = lambda *_args: self.fail('Closed-window verification must not read HTTP')
        adapter.release.update(revision='a' * 40, ui_release_sha256='0' * 64)
        target = {'api': [dict(row, **adapter.release['api_sha256']) for row in baseline['files']['api']],
            'ui': [dict(row, **adapter.release['ui_sha256'], **{'ui-release.json': '0' * 64}) for row in baseline['files']['ui']],
            'ui_revisions': ['a' * 40] * 2}
        adapter.runtime_files = lambda: target
        adapter.verify_window(baseline, {'old': True})
        adapter.fingerprint = lambda: {'new-job': True}
        with self.assertRaises(host.base.ReleaseFailure): adapter.verify_window(baseline, {'old': True})


if __name__ == '__main__': unittest.main()
