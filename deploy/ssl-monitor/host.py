"""Production adapter for the separately reviewed additive SSL publisher.

Importing this module performs no Docker, database or customer operation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys

from contract import HELPER_SOURCES, derived_release, dockerfiles, migration_path, validate_migration
from manifest import API_FILES, COMPAT_FILES
from schema import SSL_TABLES, build_probe, verify_additive
from publish import publish


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_module('ssl_publication_base', Path(__file__).resolve().parents[1] / 'upgrade-review/publish.py')
require, digest = base.require, base.digest


def tree(root):
    require(root.is_dir() and not root.is_symlink() and
            not any(p.is_symlink() for p in root.rglob('*')), 'ssl-stage-linked-or-missing')
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}


def load_stage(root, revision, previous):
    """Bind every prepared byte to the archive plus a fixed derivation grammar."""
    require(base.valid_revision(revision) and base.valid_revision(previous) and revision != previous,
            'ssl-revisions-invalid')
    original = root / ('ssl-' + revision[:7])
    prepared = root / ('ssl-prepared-' + revision[:7])
    checks_path = original / 'isolated-checks.json'
    require(checks_path.is_file() and not checks_path.is_symlink(), 'ssl-check-receipt-missing')
    checks = json.loads(checks_path.read_text())
    archive = original.with_suffix('.tar.gz')
    require(digest(archive) == checks['archive_sha256'], 'ssl-archive-hash-mismatch')
    base.bind_stage_to_archive(original, archive)
    actual_original = tree(original)
    actual_original.pop('isolated-checks.json')
    require(actual_original == checks['original_files'], 'ssl-original-tree-mismatch')
    require(checks['revision'] == revision and checks['previous_revision'] == previous
            and checks['schema_changes'] is True and all(checks.get(name) is True for name in (
                'linux_regression', 'mariadb_regression', 'migration_rehearsal', 'rollback_rehearsal',
                'release_failure_tests', 'image_tree_exact', 'rollback_image_tree_exact')), 'ssl-isolated-checks-incomplete')
    original_release = json.loads((original / 'release.json').read_text())
    require(original_release['revision'] == revision and original_release['previous_revision'] == previous
            and original_release['schema_changes'] is True, 'ssl-original-release-mismatch')
    migration = checks['migration']
    path = migration_path(migration)
    generated = prepared / 'source/spug_api' / path
    validate_migration(generated.read_text())
    release = derived_release(original_release, migration, digest(generated))
    actual_release = json.loads((prepared / 'release.json').read_text())
    require(actual_release == release, 'ssl-derived-release-mismatch')
    require(json.loads((prepared / 'api-release.json').read_text()) == {
        'revision': revision, 'migration': migration, 'sha256': release['api_sha256']}, 'ssl-overlay-manifest-mismatch')
    candidate_dockerfile, rollback_dockerfile = dockerfiles(previous, migration)
    require((prepared / 'Dockerfile').read_text() == candidate_dockerfile and
            (prepared / 'rollback.Dockerfile').read_text() == rollback_dockerfile, 'ssl-derived-dockerfile-mismatch')
    expected = dict(actual_original)
    expected['release.json'] = digest(prepared / 'release.json')
    expected['source/spug_api/' + path] = digest(generated)
    for name, origin in HELPER_SOURCES.items():
        expected[name] = digest(original / origin)
    for name in ('Dockerfile', 'rollback.Dockerfile', 'api-release.json'):
        expected[name] = digest(prepared / name)
    require(tree(prepared) == expected == checks['prepared_files'], 'ssl-prepared-tree-not-exact')
    require(set(checks['new_schema']) == set(SSL_TABLES) and
            all(base.valid_sha(value) for value in checks['new_schema'].values()), 'ssl-rehearsed-schema-incomplete')
    require(all(base.valid_sha(value) for value in release['api_sha256'].values())
            and all(base.valid_sha(value) for value in release['ui_sha256'].values()), 'ssl-manifest-hash-invalid')
    for name, value in release['api_sha256'].items():
        require(digest(prepared / 'source/spug_api' / name) == value, 'ssl-api-content-mismatch')
    ui_manifest = json.loads((prepared / 'build/ui-release.json').read_text())
    require(ui_manifest == {'revision': revision, 'sha256': release['ui_sha256']}
            and digest(prepared / 'build/ui-release.json') == release['ui_release_sha256'], 'ssl-ui-manifest-mismatch')
    for name, value in release['ui_sha256'].items():
        require(digest(base.relative_file(prepared / 'build', name)) == value, 'ssl-ui-content-mismatch')
    release.update(image='spug-baota:' + revision[:7], profile='ssl-monitor',
                   helper_sha256={name: expected[name] for name in HELPER_SOURCES})
    return prepared, release, checks


class SSLHost(base.DockerHost):
    def __init__(self, revision, previous, root=Path('/opt/spug')):
        super().__init__(revision, root)
        self.previous = previous
        self.original_stage = root / ('ssl-' + revision[:7])
        self.stage = root / ('ssl-prepared-' + revision[:7])
        self.api_attempted = self.ui_attempted = False
        self.migration_attempted = False
        self.record_number = 0

    def load(self):
        self.stage, self.release, self.checks = load_stage(self.root, self.revision, self.previous)
        require(base.available_memory() >= 256 * 1024**2 and shutil.disk_usage(self.root).free >= 2 * 1024**3,
                'ssl-insufficient-publish-resources')
        for image, expected_id, expected_revision in (
            (self.release['image'], self.checks['image_id'], self.revision),
            ('spug-baota:' + self.previous[:7], self.checks['baseline_image_id'], self.previous),
            ('spug-baota:ssl-rollback-' + self.revision[:7], self.checks['rollback_image_id'], self.previous)):
            require(re.fullmatch('sha256:[0-9a-f]{64}', expected_id) and self.run(
                ['docker', 'image', 'inspect', '--format', '{{.Id}}', image]) == expected_id, 'ssl-image-identity-mismatch')
            require(self.run(['docker', 'image', 'inspect', '--format',
                '{{index .Config.Labels "org.opencontainers.image.revision"}}', image]) == expected_revision,
                'ssl-image-revision-mismatch')
        self.preservation_probe = build_probe((self.stage / 'fingerprint.py').read_text(), self.release['migration'])

    def fingerprint(self):
        value = json.loads(self.run(['docker', 'exec', '-i', 'spug', 'python3', '-', '--require-idle'],
                                   input=self.preservation_probe, timeout=90))
        require(set(value) == {'business', 'schema', 'migrations'}, 'ssl-preservation-envelope-invalid')
        base.check_fingerprint(value['business'])
        return value

    def preflight(self):
        self.load()
        require(self.live_revision() == self.previous, 'ssl-live-revision-mismatch')
        old_compose = self.compose_text()
        self.candidate_compose = base.next_compose(old_compose, self.previous, self.revision)
        self.validate_compose()
        self.assert_running()
        before = self.fingerprint()
        require(not set(before['schema']) & set(SSL_TABLES), 'ssl-additions-already-present')
        require('monitor/' + self.release['migration'] not in before['migrations'], 'ssl-migration-already-present')
        files = self.runtime_files()
        base.verify_previous_files(files, self.release)
        self.original_compose = old_compose
        self.baseline = {'fingerprint': before, 'identity': self.identity(), 'files': files, 'compose': old_compose}
        return self.baseline

    def backup_configuration(self, baseline):
        self.setup_backup(baseline)

    def close(self):
        self.stop_health()
        super().close()

    def verify_baseline(self, baseline):
        require(self.fingerprint() == baseline['fingerprint'], 'ssl-maintenance-business-drift')
        require(self.identity() == baseline['identity'], 'ssl-maintenance-process-drift')
        require(self.runtime_files() == baseline['files'], 'ssl-maintenance-source-drift')
        require(self.compose_text() == baseline['compose'], 'ssl-maintenance-compose-drift')

    def backup_pair(self):
        super().backup()
        # The exact overlay imports its fixed path allowlist, which is staged and
        # verified separately in addition to the legacy backup primitive.
        self.run(['docker', 'cp', str(self.stage / 'manifest.py'), 'spug:' + self.destination + '/manifest.py'])
        require(self.probe({'mode': 'transfer', 'destination': self.destination,
            'expected': {'manifest.py': self.release['helper_sha256']['manifest.py']}}) == {'verified': True},
            'ssl-manifest-transfer-invalid')

    def overlay_api(self):
        self.api_attempted = True
        result = self.helper('api', 'publish')
        base.check_helper_result(result, 'api', 'publish', self.release)
        self.verify_receipt('api', self.baseline)

    def migrate(self):
        self.migration_attempted = True
        # Confirm the frozen migration encodes exactly the current source models
        # and is the only pending operation before allowing any DDL.
        self.check_model_state(pending=True)
        self.run(['docker', 'exec', '-w', '/data/spug/spug_api', 'spug', 'python3',
                  'manage.py', 'migrate', '--noinput'], timeout=180)
        self.check_model_state(pending=False)

    def check_model_state(self, pending=False):
        # Read-only model-state comparison, with no production makemigrations.
        check = """import os
os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'
import django
django.setup()
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState
from django.db.migrations.autodetector import MigrationAutodetector
executor=MigrationExecutor(connection)
plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
assert [(migration.app_label, migration.name, backwards) for migration, backwards in plan] == EXPECTED_PLAN
assert not MigrationAutodetector(executor.loader.project_state(), ProjectState.from_apps(apps)).changes(graph=executor.loader.graph)
print('Migration history and model state verified.')
"""
        expected = [('monitor', self.release['migration'], False)] if pending else []
        check = check.replace('EXPECTED_PLAN', repr(expected))
        self.run(['docker', 'exec', '-i', '-w', '/data/spug/spug_api', 'spug', 'python3', '-'],
                 input=check, timeout=90)

    def verify_additions(self, baseline):
        self.check_model_state(pending=False)
        after = self.fingerprint()
        verify_additive(baseline['fingerprint'], after, self.release['migration'])
        require({name: after['schema'][name] for name in SSL_TABLES} == self.checks['new_schema'],
                'ssl-applied-schema-differs-from-rehearsal')
        require(self.identity() == baseline['identity'], 'ssl-original-process-drift')
        return after

    def overlay_ui(self):
        self.ui_attempted = True
        result = self.helper('ui', 'publish')
        base.check_helper_result(result, 'ui', 'publish', self.release)
        self.verify_receipt('ui', self.baseline)

    def verify_candidate(self, baseline, upgraded):
        require(self.fingerprint() == upgraded, 'ssl-candidate-business-drift')
        require(self.identity() == baseline['identity'], 'ssl-candidate-process-drift')
        base.verify_target_files(self.runtime_files(), baseline['files'], self.release)

    def compose_candidate(self):
        require(self.compose_text() == self.original_compose, 'ssl-compose-drift')
        self.write_compose(self.candidate_compose)
        self.validate_compose()

    def start_candidate(self): self.start_backend()
    def open(self): self.open_ingress()

    def verify_open(self, baseline, upgraded):
        self.assert_running()
        self.verify_http(self.revision)
        self.verify_candidate(baseline, upgraded)

    def verify_window(self, baseline, upgraded):
        # Ingress is closed here. Check durable jobs and business instead of
        # attempting an HTTP request against a deliberately stopped nginx.
        require(self.fingerprint() == upgraded, 'ssl-post-ingress-business-drift')
        require(self.identity() == baseline['identity'], 'ssl-post-ingress-process-drift')
        base.verify_target_files(self.runtime_files(), baseline['files'], self.release)

    def restore(self, baseline, compatibility):
        if self.ui_attempted:
            self.verify_receipt('ui', baseline)
            result = self.helper('ui', 'restore')
            base.check_helper_result(result, 'ui', 'restore', self.release)
            self.verify_receipt('ui', baseline)
        if self.api_attempted:
            self.verify_receipt('api', baseline)
            mode = 'restore-compat' if compatibility else 'restore'
            result = self.helper('api', mode)
            require(result == {'revision': self.revision, 'mode': mode, 'modules': len(self.release['api_sha256'])},
                    'ssl-api-restoration-result-invalid')
            self.verify_receipt('api', baseline)
        compose = baseline['compose']
        if compatibility:
            compose = compose.replace('spug-baota:' + self.previous[:7], 'spug-baota:ssl-rollback-' + self.revision[:7])
        self.write_compose(compose)
        self.validate_compose()

    def verify_restored(self, baseline, upgraded, compatibility):
        actual = self.fingerprint()
        require(actual == (upgraded if compatibility else baseline['fingerprint']), 'ssl-restored-business-or-schema-drift')
        require(self.identity() == baseline['identity'], 'ssl-restored-original-process-drift')
        runtime = self.runtime_files()
        expected_api = baseline['files']['api']
        if compatibility:
            keep = set(COMPAT_FILES) | {migration_path(self.release['migration'])}
            expected_api = [dict(original, **{name: self.release['api_sha256'][name] for name in keep})
                            for original in expected_api]
        expected = dict(baseline['files'], api=expected_api)
        base.verify_restored_files(runtime, expected, self.release['ui_sha256'])
        expected_compose = baseline['compose'] if not compatibility else baseline['compose'].replace(
            'spug-baota:' + self.previous[:7], 'spug-baota:ssl-rollback-' + self.revision[:7])
        require(self.compose_text() == expected_compose, 'ssl-restored-compose-drift')

    def start_restored(self, compatibility): self.start_backend()
    def verify_old_http(self):
        self.assert_running()
        self.verify_http(self.previous)

    def record(self, report):
        self.record_number += 1
        value = dict(report, revision=self.revision, previous_revision=self.previous,
                     backup=str(self.backup_directory), services_running=9 if not report['maintenance_closed'] else None)
        self.save('ssl-publication-' + str(self.record_number) + '.json', value)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not all(base.valid_revision(value) for value in args) or args[0] == args[1]:
        print(json.dumps({'published': False, 'failure': 'two-distinct-40hex-revisions-required'}))
        return 2
    os.umask(0o077)
    try:
        with base.publication_lock():
            host = SSLHost(*args)
            result = publish(host)
            result.update(revision=args[0], previous_revision=args[1], backup=str(host.backup_directory))
    except BaseException:
        result = {'published': False, 'failure': 'ssl-publication-not-started'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('published') else 1


if __name__ == '__main__': sys.exit(main())
