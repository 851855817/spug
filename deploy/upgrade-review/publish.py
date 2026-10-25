"""No-schema-change hot release with a fail-closed, receipt-verified rollback.

This module is import-safe. The controller accepts a host adapter so failure
paths can be exercised without Docker, a database, or customer access.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid


MODULE = 'apps/baota/operations/php_update.py'
MAINTENANCE = ('nginx', 'spug-api', 'spug-ws', 'spug-panel-worker',
               'spug-ops-worker', 'spug-scheduler')
PRESERVED = ('redis', 'spug-worker', 'spug-monitor')
BACKEND = tuple(name for name in MAINTENANCE if name != 'nginx')
ENTRIES = ('index.html', 'asset-manifest.json', 'ui-release.json')
PAIRED_ARCHIVES = ('database.sql.gz', 'spug-data.tar.gz')
HELPERS = ('apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py')
RUNTIME_ROOTS = {'api': ['/opt/spug/spug_api', '/data/spug/spug_api'],
                 'ui': ['/opt/spug/spug_web/build', '/data/spug/spug_web/build']}


class ReleaseFailure(Exception):
    """Only fixed, non-sensitive failure codes cross the CLI boundary."""


def require(condition, code):
    if not condition:
        raise ReleaseFailure(code)


def digest(path):
    state = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            state.update(block)
    return state.hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def valid_revision(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None


def verify_backup_manifests(directory):
    """Verify the fixed health manifest and the separate configuration receipt."""
    for name in ('SHA256SUMS', 'backup-content.json'):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'backup-receipt-invalid')
    hashes = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (database\.sql\.gz|spug-data\.tar\.gz)', line)
        require(match is not None and match[2] not in hashes, 'paired-backup-manifest-invalid')
        hashes[match[2]] = match[1]
    require(set(hashes) == set(PAIRED_ARCHIVES), 'paired-backup-manifest-incomplete')
    configuration = json.loads((directory / 'backup-content.json').read_text())['sha256']
    require(isinstance(configuration, dict) and configuration, 'configuration-backup-receipt-empty')
    for name, expected in configuration.items():
        require(isinstance(name, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', name) and
                name not in set(PAIRED_ARCHIVES) | {'SHA256SUMS', 'backup-content.json'} and
                valid_sha(expected), 'configuration-backup-receipt-invalid')
    hashes.update(configuration)
    for name, expected in hashes.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and digest(path) == expected,
                'backup-content-hash-mismatch')


def write_backup_manifests(directory):
    """Keep SHA256SUMS compatible with the existing host health diagnostic."""
    hashes = {}
    for path in sorted(directory.iterdir()):
        require(path.is_file() and not path.is_symlink() and
                re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', path.name), 'backup-file-invalid')
        path.chmod(0o600)
        hashes[path.name] = digest(path)
    require(set(PAIRED_ARCHIVES) <= set(hashes), 'paired-backup-files-missing')
    checksum = directory / 'SHA256SUMS'
    with checksum.open('x') as stream:
        checksum.chmod(0o600)
        stream.write(''.join(hashes[name] + '  ' + name + '\n' for name in PAIRED_ARCHIVES))
    receipt = directory / 'backup-content.json'
    with receipt.open('x') as stream:
        receipt.chmod(0o600)
        json.dump({'sha256': {name: expected for name, expected in hashes.items()
                             if name not in PAIRED_ARCHIVES}}, stream, sort_keys=True, indent=2)
    verify_backup_manifests(directory)


def available_memory():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise ReleaseFailure('memory-availability-unknown')


@contextmanager
def publication_lock(path=Path('/opt/spug/upgrade-publish.lock')):
    """Hold a process-wide, nonblocking Linux lock through publication/recovery."""
    stream = None
    locked = False
    try:
        # Import lazily: local Windows test collection must remain import-safe.
        import fcntl
        require(path.parent.is_dir() and not path.is_symlink() and
                all(not parent.is_symlink() for parent in path.parents), 'publisher-lock-path-invalid')
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) |
                             getattr(os, 'O_CLOEXEC', 0), 0o600)
        stream = os.fdopen(descriptor, 'a+')
        path.chmod(0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
    except BaseException:
        if stream is not None:
            stream.close()
        raise ReleaseFailure('publisher-lock-unavailable') from None
    try:
        yield
    finally:
        try:
            if locked:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def next_compose(text, previous, revision):
    old = 'spug-baota:' + previous[:7]
    pattern = re.compile(r'(?m)^([ \t]*image:[ \t]*)' + re.escape(old) +
                         r'([ \t]*(?:#[^\r\n]*)?)(\r?)$')
    require(len(list(pattern.finditer(text))) == 1 and text.count(old) == 1,
            'previous-compose-image-mismatch')
    return pattern.sub(lambda match: match[1] + 'spug-baota:' + revision[:7] +
                       match[2] + match[3], text)


def relative_file(root, name):
    require(isinstance(name, str) and '\\' not in name and ':' not in name,
            'unsafe-stage-path')
    relative = PurePosixPath(name)
    require(name and not relative.is_absolute() and '..' not in relative.parts and
            name == relative.as_posix(), 'unsafe-stage-path')
    path = root.joinpath(*relative.parts)
    require(not root.is_symlink() and path.is_file() and not path.is_symlink(),
            'missing-stage-file')
    require(all(not part.is_symlink() for part in path.parents),
            'stage-symlink')
    return path


def bind_stage_to_archive(stage, archive):
    """Compare every staged byte to the immutable, bounded prepared archive."""
    archived_files = set()
    seen = set()
    total = 0
    try:
        with tarfile.open(archive, 'r:gz') as source:
            for number, member in enumerate(source, start=1):
                total += member.size
                require(number <= 20000 and 0 <= total <= 500 * 1024**2,
                        'prepared-archive-exceeds-limits')
                path = PurePosixPath(member.name)
                require(path.parts and not path.is_absolute() and '..' not in path.parts and
                        '\\' not in member.name and ':' not in member.name and
                        path.parts[0] == stage.name and member.name == path.as_posix() and
                        member.name not in seen and (member.isfile() or member.isdir()),
                        'prepared-archive-member-invalid')
                seen.add(member.name)
                relative = PurePosixPath(*path.parts[1:])
                destination = stage.joinpath(*relative.parts)
                if member.isdir():
                    require(destination.is_dir() and not destination.is_symlink(),
                            'archived-stage-directory-missing')
                    continue
                name = relative.as_posix()
                require(name != 'isolated-checks.json', 'archive-cannot-supply-check-receipt')
                destination = relative_file(stage, name)
                require(destination.stat().st_size == member.size, 'archived-stage-size-mismatch')
                state = hashlib.sha256()
                with source.extractfile(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        state.update(block)
                require(digest(destination) == state.hexdigest(), 'archived-stage-content-mismatch')
                archived_files.add(name)
    except (tarfile.TarError, OSError):
        raise ReleaseFailure('prepared-archive-unreadable') from None
    staged_files = {path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file()}
    require(archived_files and staged_files == archived_files | {'isolated-checks.json'},
            'unarchived-stage-file')


def load_stage(stage, revision, previous):
    require(valid_revision(revision) and valid_revision(previous) and revision != previous,
            'invalid-revisions')
    require(stage.is_dir() and not stage.is_symlink(), 'missing-stage')
    # A symlink anywhere in the exported source or build must not evade the
    # per-manifest checks when docker cp stages the complete directories.
    require(not any(path.is_symlink() for path in stage.rglob('*')), 'stage-symlink')
    release = json.loads(relative_file(stage, 'release.json').read_text())
    checks = json.loads(relative_file(stage, 'isolated-checks.json').read_text())
    require(release['revision'] == revision and release['previous_revision'] == previous,
            'stage-revision-mismatch')
    image = 'spug-baota:' + revision[:7]
    require(release['image'] == image and checks['revision'] == revision and
            release['schema_changes'] is False and checks['schema_changes'] is False,
            'isolated-checks-mismatch')
    require(checks['image'] == image and checks['previous_revision'] == previous,
            'isolated-baseline-or-image-mismatch')
    require(all(checks[key] is True for key in
                ('linux_regression', 'release_failure_tests', 'image_wiring')),
            'isolated-checks-incomplete')
    require(isinstance(checks['image_id'], str) and
            re.fullmatch('sha256:[0-9a-f]{64}', checks['image_id']) is not None,
            'prepared-image-id-invalid')
    archive = stage.with_suffix('.tar.gz')
    require(valid_sha(checks['archive_sha256']) and archive.is_file() and not archive.is_symlink() and
            digest(archive) == checks['archive_sha256'], 'prepared-archive-mismatch')
    bind_stage_to_archive(stage, archive)
    for key in ('api_sha256', 'previous_api_sha256'):
        require(set(release[key]) == {MODULE} and all(valid_sha(v) for v in release[key].values()),
                'api-overlay-must-be-single-php-module')
    api = json.loads(relative_file(stage, 'api-release.json').read_text())
    require(api['revision'] == revision and api['sha256'] == release['api_sha256'],
            'api-manifest-mismatch')
    require(digest(relative_file(stage / 'source/spug_api', MODULE)) == release['api_sha256'][MODULE],
            'stage-api-hash-mismatch')
    require(set(release['helper_sha256']) == set(HELPERS), 'helper-manifest-incomplete')
    for name, expected in release['helper_sha256'].items():
        require(valid_sha(expected) and digest(relative_file(stage, name)) == expected,
                'helper-hash-mismatch')
    ui_path = relative_file(stage / 'build', 'ui-release.json')
    ui = json.loads(ui_path.read_text())
    require(ui['revision'] == revision and {'index.html', 'asset-manifest.json'} <= set(ui['sha256']),
            'ui-manifest-incomplete')
    require('ui-release.json' not in ui['sha256'], 'recursive-ui-manifest')
    for name, expected in ui['sha256'].items():
        require(valid_sha(expected) and digest(relative_file(stage / 'build', name)) == expected,
                'stage-ui-hash-mismatch')
    build_files = {path.relative_to(stage / 'build').as_posix()
                   for path in (stage / 'build').rglob('*') if path.is_file()}
    require(build_files == set(ui['sha256']) | {'ui-release.json'}, 'unlisted-ui-stage-file')
    return dict(release, ui_sha256=ui['sha256'], ui_release_sha256=digest(ui_path),
                prepared_image_id=checks['image_id'])


def check_fingerprint(value):
    require(isinstance(value, dict) and valid_sha(value.get('schema_sha256')) and
            valid_sha(value.get('migration_rows_sha256')) and
            value.get('credentials_decrypt') is True and
            valid_sha(value.get('baota.key')) and valid_sha(value.get('baota.key.sha256')),
            'fingerprint-incomplete')
    models = []
    for key, record in value.items():
        if isinstance(record, dict):
            require(set(record) == {'count', 'digest'} and type(record['count']) is int and
                    record['count'] >= 0 and valid_sha(record['digest']), 'fingerprint-model-invalid')
            models.append(key)
        elif key != 'credentials_decrypt':
            require(valid_sha(record), 'fingerprint-non-digest-value')
    require(models, 'fingerprint-models-missing')


def verify_previous_files(current, release):
    require(len(current['api']) == len(current['ui']) == 2 and
            current['ui_revisions'] == [release['previous_revision']] * 2,
            'previous-runtime-ui-mismatch')
    for root in current['api']:
        require(all(root.get(name) == expected for name, expected in
                    release['previous_api_sha256'].items()), 'previous-runtime-api-mismatch')
    for root in current['ui']:
        # The existing static helper backs up entrypoints only. Shared assets
        # may be reused unchanged, but cannot be overwritten reversibly.
        require(all(name in ENTRIES or name not in root or root[name] == expected
                    for name, expected in release['ui_sha256'].items()),
                'existing-static-asset-would-be-overwritten')


def verify_target_files(current, baseline, release):
    require(current['ui_revisions'] == [release['revision']] * 2, 'target-runtime-ui-mismatch')
    for actual, original in zip(current['api'], baseline['api']):
        require(actual == dict(original, **release['api_sha256']), 'target-api-files-mismatch')
    for actual, original in zip(current['ui'], baseline['ui']):
        expected = dict(original, **release['ui_sha256'])
        expected['ui-release.json'] = release['ui_release_sha256']
        require(actual == expected, 'target-ui-files-mismatch')


def verify_restored_files(current, baseline, staged_ui):
    require(current['api'] == baseline['api'] and
            current['ui_revisions'] == baseline['ui_revisions'], 'restore-api-or-ui-revision-mismatch')
    require(len(current['ui']) == len(baseline['ui']) == 2, 'restore-roots-missing')
    for actual, original in zip(current['ui'], baseline['ui']):
        require(all(actual.get(name) == expected for name, expected in original.items()),
                'restore-original-static-file-mismatch')
        for name in set(actual) - set(original):
            # Existing helper intentionally retains new assets for open pages.
            # Missing old entrypoints must still be removed by its restore.
            require(name not in ENTRIES and staged_ui.get(name) == actual[name],
                    'restore-unexpected-static-file')


def check_helper_result(result, kind, mode, release):
    require(isinstance(result, dict) and result.get('revision') == release['revision'] and
            result.get('mode') == mode, 'helper-result-invalid')
    if kind == 'api':
        require(result.get('modules') == 1, 'helper-api-module-count-invalid')
    else:
        require(result.get('verified_files') == len(release['ui_sha256']) + 1,
                'helper-ui-file-count-invalid')


def publish_release(host, revision, previous):
    """Publish, or restore only after proving that old workers may safely resume."""
    report = {'revision': revision if valid_revision(revision) else None,
              'published': False, 'rolled_back': False, 'errors': []}
    baseline = release = None
    attempted = []
    maintenance_started = ingress_attempted = False
    step = 'validate-stage'
    try:
        release = host.load_release(revision, previous)
        step = 'preflight'
        require(host.live_revision() == previous, 'previous-live-ui-mismatch')
        old_compose = host.compose_text()
        new_compose = next_compose(old_compose, previous, revision)
        # Reject existing invalid configuration while the healthy deployment
        # is still untouched; rollback must be able to validate this baseline.
        host.validate_compose()
        host.assert_running()
        before = host.fingerprint()  # Adapter always invokes --require-idle.
        check_fingerprint(before)
        baseline = {'fingerprint': before, 'identity': host.identity(),
                    'files': host.runtime_files(), 'compose': old_compose}
        verify_previous_files(baseline['files'], release)
        step = 'prepare-backup'
        report['backup'] = host.setup_backup(baseline)
        step = 'enter-maintenance'
        maintenance_started = True  # A failed systemctl may already have acted.
        host.stop_health()
        host.close()
        require(host.fingerprint() == before, 'maintenance-business-drift')
        require(host.identity() == baseline['identity'], 'maintenance-process-drift')
        require(host.runtime_files() == baseline['files'], 'maintenance-file-drift')
        require(host.compose_text() == old_compose, 'maintenance-compose-drift')
        step = 'paired-backup'
        host.backup()
        # A complete paired backup is the final gate before runtime file writes.
        for kind in ('api', 'ui'):
            step = kind + '-publication'
            attempted.append(kind)  # Also restore helpers which raised midway.
            result = host.helper(kind, 'publish')
            check_helper_result(result, kind, 'publish', release)
            host.verify_receipt(kind, baseline)
        step = 'verify-prepared-release'
        require(host.fingerprint() == before, 'publication-business-or-schema-drift')
        require(host.identity() == baseline['identity'], 'publication-process-drift')
        verify_target_files(host.runtime_files(), baseline['files'], release)
        step = 'future-recreate-compose'
        require(host.compose_text() == old_compose, 'publication-compose-drift')
        host.write_compose(new_compose)
        host.validate_compose()
        step = 'start-new-release'
        host.start_backend()
        # Do not open ingress if starting a scheduler or worker exposed drift.
        require(host.fingerprint() == before, 'backend-start-business-drift')
        require(host.identity() == baseline['identity'], 'backend-start-process-drift')
        ingress_attempted = True
        host.open_ingress()
        step = 'verify-open-release'
        host.assert_running()
        host.verify_http(revision)
        require(host.fingerprint() == before, 'open-release-business-or-schema-drift')
        require(host.identity() == baseline['identity'], 'open-release-process-drift')
        verify_target_files(host.runtime_files(), baseline['files'], release)
        report.update(published=True, services_running=9, http=200,
                      schema_and_business_unchanged=True, original_processes=True)
        host.record('publication', report)
        step = 'resume-health'
        host.resume_health()
        return report
    except BaseException:
        # Never serialize an exception or command output: database credentials
        # and decrypted application configuration may exist in local variables.
        report['published'] = False
        report['failure'] = step
        if not maintenance_started:
            report['recovery'] = 'not-required'
            return report

    def attempt(code, action):
        try:
            action()
            return True
        except BaseException:
            report['errors'].append(code)
            return False

    # Disable auto-recovery again, including failures while resuming it.
    health_stopped = attempt('health-stop-unverified', host.stop_health)
    closed = attempt('maintenance-stop-unverified', host.close)
    report['maintenance_closed'] = closed and health_stopped
    if not report['maintenance_closed']:
        return report

    if ingress_attempted:
        # Source restoration would downgrade the health_policy verifier. Even
        # a new queued target or unrelated business write makes that unsafe.
        def verify_rollback_window():
            require(host.fingerprint() == baseline['fingerprint'], 'post-ingress-business-drift')
            require(host.identity() == baseline['identity'], 'post-ingress-process-drift')
        if not attempt('rollback-window-unverified-no-downgrade', verify_rollback_window):
            attempt('rollback-record-failed', lambda: host.record('rollback', report))
            return report

    for kind in reversed(attempted):
        # Always invoke the real restore helper, even if publication or receipt
        # validation failed. Both helpers validate the complete backup first.
        attempt(kind + '-backup-receipt-unverified', lambda kind=kind: host.verify_receipt(kind, baseline))
        def restore(kind=kind):
            result = host.helper(kind, 'restore')
            check_helper_result(result, kind, 'restore', release)
            host.verify_receipt(kind, baseline)
        attempt(kind + '-restore-unverified', restore)

    def verify_restoration():
        require(host.fingerprint() == baseline['fingerprint'], 'restore-business-or-schema-drift')
        require(host.identity() == baseline['identity'], 'restore-process-drift')
        verify_restored_files(host.runtime_files(), baseline['files'], release['ui_sha256'])
    attempt('complete-restoration-unverified', verify_restoration)
    attempt('compose-restore-failed', lambda: host.write_compose(baseline['compose']))
    attempt('compose-restore-invalid', host.validate_compose)
    if report['errors']:
        attempt('rollback-record-failed', lambda: host.record('rollback', report))
        return report

    try:
        # All original file hashes, prior UI revision, business/schema/key
        # digests and idle state were checked before any old worker can start.
        host.record('rollback-ready', report)
        host.start_backend()
        require(host.fingerprint() == baseline['fingerprint'], 'restored-backend-business-drift')
        require(host.identity() == baseline['identity'], 'restored-backend-process-drift')
        host.open_ingress()
        host.assert_running()
        host.verify_http(previous)
        verify_restoration()
        report.update(rolled_back=True, maintenance_closed=False)
        host.record('rollback', report)
        host.resume_health()
    except BaseException:
        report.update(rolled_back=False, maintenance_closed=False)
        report['errors'].append('restored-service-verification-failed')
        health_stopped = attempt('health-stop-unverified', host.stop_health)
        closed = attempt('maintenance-stop-unverified', host.close)
        report['maintenance_closed'] = health_stopped and closed
        attempt('rollback-record-failed', lambda: host.record('rollback', report))
    return report


# The same read-only file/receipt probe is run inside the existing container and
# in real temporary-directory tests. It never returns source or customer data.
FILE_PROBE = r'''
import hashlib, json, os, sys
from pathlib import Path, PurePosixPath

def sha(path):
    state = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            state.update(block)
    return state.hexdigest()

def scan(root, kind):
    assert root.is_dir() and not root.is_symlink()
    result = {}
    for directory, folders, names in os.walk(root):
        base = Path(directory)
        assert all(not (base / name).is_symlink() for name in folders + names)
        # Supervisor worker output is deliberately mutable while the legacy
        # workers stay alive. Only this API-root log directory is excluded;
        # nested source directories named logs and UI files remain protected.
        folders[:] = sorted(name for name in folders if name != '__pycache__'
                            and not (kind == 'api' and base == root and name == 'logs'))
        for name in sorted(names):
            path = base / name
            if name.endswith(('.pyc', '.pyo')):
                continue
            assert path.is_file()
            result[path.relative_to(root).as_posix()] = sha(path)
    return result

try:
    request = json.loads(sys.argv[1])
    if request['mode'] == 'snapshot':
        roots = request['roots']
        assert set(roots) == {'api', 'ui'} and all(len(v) == 2 for v in roots.values())
        result = {kind: [scan(Path(root), kind) for root in paths] for kind, paths in roots.items()}
        result['ui_revisions'] = [json.loads((Path(root) / 'ui-release.json').read_text())['revision']
                                  for root in roots['ui']]
    elif request['mode'] == 'receipt':
        backup = Path(request['backup'])
        assert backup.is_dir() and not backup.is_symlink()
        receipt_path = backup / 'complete.json'
        assert receipt_path.is_file() and not receipt_path.is_symlink()
        receipt = json.loads(receipt_path.read_text())
        assert receipt['revision'] == request['revision']
        assert receipt['originals'] == request['expected']
        for name, expected in request['expected'].items():
            relative = PurePosixPath(name)
            assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
            saved = backup.joinpath(*relative.parts)
            assert not saved.is_symlink()
            assert all(not p.is_symlink() for p in saved.parents)
            if expected is None:
                assert not saved.exists()
            else:
                assert sha(saved) == expected
        result = {'verified': True}
    elif request['mode'] == 'stage':
        destination = Path(request['destination'])
        assert not destination.exists() and not destination.is_symlink()
        for path in request['backups']:
            assert not Path(path).exists() and not Path(path).is_symlink()
        destination.mkdir(mode=0o700)
        result = {'verified': True}
    elif request['mode'] == 'transfer':
        destination = Path(request['destination'])
        assert destination.is_dir() and not destination.is_symlink()
        for name, expected in request['expected'].items():
            relative = PurePosixPath(name)
            assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
            path = destination.joinpath(*relative.parts)
            assert path.is_file() and not path.is_symlink()
            assert all(not parent.is_symlink() for parent in path.parents)
            assert sha(path) == expected
        result = {'verified': True}
    else:
        raise ValueError('unknown mode')
    print(json.dumps(result, sort_keys=True))
except BaseException:
    print(json.dumps({'verified': False}))
    sys.exit(1)
'''


class DockerHost:
    def __init__(self, revision, root=Path('/opt/spug')):
        self.revision = revision
        self.root = root
        self.stage = root / ('upgrade-' + revision[:7])
        self.compose = root / 'docker-compose.yml'
        self.destination = '/tmp/spug-ui-' + revision[:7]
        self.backup_directory = None
        self.release = None
        self.health_state = None

    def run(self, command, *, input=None, timeout=120, check=True, stdout=None):
        try:
            process = subprocess.run(command, input=input, text=stdout is None,
                                     stdout=subprocess.PIPE if stdout is None else stdout,
                                     stderr=subprocess.DEVNULL, timeout=timeout)
            require(not check or process.returncode == 0, 'host-command-failed')
            return process.stdout.strip() if stdout is None else process.returncode
        except BaseException:
            raise ReleaseFailure('host-command-failed') from None

    def control(self, action, *services, check=True):
        return self.run(['docker', 'exec', 'spug', 'supervisorctl', action, *services], check=check)

    def probe(self, request):
        return json.loads(self.run(['docker', 'exec', '-i', 'spug', 'python3', '-',
                                    json.dumps(request)], input=FILE_PROBE))

    def load_release(self, revision, previous):
        self.release = load_stage(self.stage, revision, previous)
        require(available_memory() >= 256 * 1024**2 and
                shutil.disk_usage(self.root).free >= 2 * 1024**3, 'insufficient-publish-resources')
        label = self.run(['docker', 'image', 'inspect', '--format',
                          '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                          self.release['image']])
        require(label == revision, 'prepared-image-revision-mismatch')
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.release['image']])
        require(image_id == self.release['prepared_image_id'], 'prepared-image-id-mismatch')
        return self.release

    def live_revision(self):
        with urllib.request.urlopen('http://127.0.0.1:8080/ui-release.json', timeout=5) as response:
            require(response.status == 200, 'live-ui-http-failed')
            return json.load(response)['revision']

    def compose_text(self):
        require(self.compose.is_file() and not self.compose.is_symlink(), 'compose-missing-or-symlink')
        return self.compose.read_text()

    def services(self):
        result = {}
        # Supervisor uses LSB status 3 when any program is stopped. During
        # maintenance that is expected; the complete parsed state is the gate.
        for line in self.control('status', check=False).splitlines():
            fields = line.split()
            require(len(fields) >= 2 and fields[0] not in result, 'supervisor-status-invalid')
            result[fields[0]] = fields[1]
        require(set(result) == set(MAINTENANCE + PRESERVED), 'supervisor-nine-services-mismatch')
        return result

    def assert_running(self):
        require(all(state == 'RUNNING' for state in self.services().values()), 'services-not-running')

    def fingerprint(self):
        result = json.loads(self.run(['docker', 'exec', '-i', 'spug', 'python3', '-', '--require-idle'],
                                     input=(self.stage / 'fingerprint.py').read_text(), timeout=90))
        check_fingerprint(result)
        return result

    def health_configuration(self):
        config = self.root / 'platform-health/spug-health.py'
        require(config.is_file() and not config.is_symlink(), 'health-config-missing')
        units = self.run(['systemctl', 'cat', 'spug-platform-health.timer', 'spug-platform-health.service'])
        return {'script_sha256': digest(config),
                'units_sha256': hashlib.sha256(units.encode()).hexdigest()}

    def identity(self):
        pids = {name: self.control('pid', name) for name in PRESERVED}
        require(all(re.fullmatch('[1-9][0-9]*', pid) for pid in pids.values()), 'preserved-process-missing')
        return {'pids': pids,
                'container': self.run(['docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', 'spug']),
                'supervisor_sha256': self.run(['docker', 'exec', 'spug', 'sha256sum', '/etc/supervisord.conf']).split()[0],
                'health': self.health_configuration()}

    def runtime_files(self):
        return self.probe({'mode': 'snapshot', 'roots': RUNTIME_ROOTS})

    def save(self, name, value):
        destination = self.backup_directory / name
        with destination.open('x', encoding='utf-8') as stream:
            destination.chmod(0o600)
            json.dump(value, stream, indent=2, sort_keys=True)

    def setup_backup(self, baseline):
        parent = self.root / 'backups'
        require(not parent.is_symlink(), 'backup-parent-symlink')
        parent.mkdir(mode=0o700, exist_ok=True)
        parent.chmod(0o700)
        # Existing host diagnostics order backup directories by the server's
        # local YYYYMMDD-HHMMSS names (UTC+8 on deployment). Use the same
        # local clock so a new pair cannot sort before an older backup.
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.backup_directory = parent / (stamp + '-before-upgrade-' + self.revision[:7] + '-' + uuid.uuid4().hex[:8])
        self.backup_directory.mkdir(mode=0o700)
        for source, name in ((self.compose, 'docker-compose.yml'),
                             (self.root / 'platform-health/spug-health.py', 'spug-health.py')):
            destination = self.backup_directory / name
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
        units = self.run(['systemctl', 'cat', 'spug-platform-health.timer', 'spug-platform-health.service'])
        self.save('health-units.json', {'configuration': units})
        self.health_state = {unit: self.run(['systemctl', 'is-active', unit], check=False)
                             for unit in ('spug-platform-health.timer', 'spug-platform-health.service')}
        require(all(state in ('active', 'inactive') for state in self.health_state.values()),
                'health-original-state-unknown')
        self.save('health-state.json', self.health_state)
        self.save('preservation-before.json', baseline)
        self.run(['docker', 'cp', 'spug:/etc/supervisord.conf', str(self.backup_directory / 'supervisord.conf')])
        (self.backup_directory / 'supervisord.conf').chmod(0o600)
        return str(self.backup_directory)

    def stop_health(self):
        self.run(['systemctl', 'stop', 'spug-platform-health.timer', 'spug-platform-health.service'])
        for unit in ('spug-platform-health.timer', 'spug-platform-health.service'):
            require(self.run(['systemctl', 'is-active', unit], check=False) == 'inactive',
                    'health-stop-unverified')

    def close(self):
        # supervisorctl returns nonzero for an already-stopped process. Query
        # every service after the stop instead of inferring state from its rc.
        self.control('stop', *MAINTENANCE, check=False)
        services = self.services()
        require(all(services[name] in ('STOPPED', 'EXITED') for name in MAINTENANCE) and
                all(services[name] == 'RUNNING' for name in PRESERVED), 'maintenance-stop-unverified')
        require(all(self.control('pid', name, check=False) == '0' for name in MAINTENANCE),
                'maintenance-process-still-present')

    def backup(self):
        # Import only the hash-validated staged copy of the existing primitive.
        spec = importlib.util.spec_from_file_location('upgrade_release_safety', self.stage / 'release_safety.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        db_read = ("import os,sys,json; sys.path.insert(0,'/data/spug/spug_api'); "
                   "os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'; import django; django.setup(); "
                   "from django.conf import settings; d=settings.DATABASES['default']; "
                   "print(json.dumps({k:d[k] for k in ('NAME','USER','PASSWORD')}))")
        credentials = json.loads(self.run(['docker', 'exec', 'spug', 'python3', '-c', db_read]))
        require(re.fullmatch('[A-Za-z0-9_]+', credentials['NAME']) is not None, 'database-name-invalid')
        def option(value):
            require(isinstance(value, str) and '\0' not in value, 'database-option-invalid')
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
        option_file = '/tmp/upgrade-backup-' + self.revision[:7] + '.cnf'
        options = '[client]\nuser=' + option(credentials['USER']) + '\npassword=' + option(credentials['PASSWORD']) + '\n'
        option_attempted = False
        try:
            option_attempted = True
            self.run(['docker', 'exec', '-i', 'spug-db', 'sh', '-c',
                      'umask 077; set -C; cat > ' + option_file], input=options)
            command = ['docker', 'exec', 'spug-db', 'mariadb-dump', '--defaults-extra-file=' + option_file,
                       '--single-transaction', '--routines', '--events', '--databases', credentials['NAME']]
            module.write_database_backup(command, self.backup_directory)
        finally:
            if option_attempted:
                self.run(['docker', 'exec', 'spug-db', 'rm', '-f', option_file])
            del credentials, options
        archive = self.backup_directory / 'spug-data.tar.gz'
        with archive.open('xb') as output:
            archive.chmod(0o600)
            self.run(['docker', 'exec', 'spug', 'tar', '-C', '/data', '-czf', '-', '.'],
                     stdout=output, timeout=180)
        for name in PAIRED_ARCHIVES:
            path = self.backup_directory / name
            path.chmod(0o600)
            require(path.stat().st_size > 100, 'backup-archive-empty')
        self.run(['gzip', '-t', str(self.backup_directory / 'database.sql.gz')])
        self.run(['tar', '-tzf', str(archive)], stdout=subprocess.DEVNULL)
        write_backup_manifests(self.backup_directory)
        # Stage only after both archives and all saved configuration are valid.
        self.probe({'mode': 'stage', 'destination': self.destination,
                    'backups': ['/data/spug-api-backups/' + self.revision,
                                '/data/spug-ui-backups/' + self.revision]})
        for name in ('build', 'source', 'api-release.json', 'apply-expiry.py', 'deploy-compact-ui.py'):
            self.run(['docker', 'cp', str(self.stage / name), 'spug:' + self.destination + '/' + name])
        expected = {'source/spug_api/' + name: expected
                    for name, expected in self.release['api_sha256'].items()}
        expected.update({'build/' + name: expected for name, expected in self.release['ui_sha256'].items()})
        expected.update({'build/ui-release.json': self.release['ui_release_sha256'],
                         'api-release.json': digest(self.stage / 'api-release.json')})
        expected.update({name: self.release['helper_sha256'][name]
                         for name in ('apply-expiry.py', 'deploy-compact-ui.py')})
        require(self.probe({'mode': 'transfer', 'destination': self.destination,
                            'expected': expected}) == {'verified': True}, 'staged-transfer-invalid')

    def helper(self, kind, mode):
        name = 'apply-expiry.py' if kind == 'api' else 'deploy-compact-ui.py'
        return json.loads(self.run(['docker', 'exec', 'spug', 'python3',
                                    self.destination + '/' + name, self.revision, mode]))

    def verify_receipt(self, kind, baseline):
        names = self.release['api_sha256'] if kind == 'api' else ENTRIES
        expected = {str(index) + '/' + name: root.get(name)
                    for index, root in enumerate(baseline['files'][kind]) for name in names}
        result = self.probe({'mode': 'receipt', 'revision': self.revision,
                             'backup': '/data/spug-' + kind + '-backups/' + self.revision,
                             'expected': expected})
        require(result == {'verified': True}, 'helper-backup-receipt-invalid')

    def write_compose(self, content):
        require(not self.compose.is_symlink(), 'compose-symlink')
        temporary = self.compose.with_name('.docker-compose.upgrade-' + self.revision[:7])
        with temporary.open('x') as stream:
            temporary.chmod(0o600)
            stream.write(content)
        temporary.replace(self.compose)

    def validate_compose(self):
        self.run(['docker', 'compose', '-f', str(self.compose), 'config', '--quiet'])

    def start_backend(self):
        self.control('start', *BACKEND)

    def open_ingress(self):
        self.control('start', 'nginx')

    def verify_http(self, revision):
        for attempt in range(15):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8080/api/account/login/', timeout=5) as response:
                    require(response.status == 200, 'local-http-failed')
                require(self.live_revision() == revision, 'local-ui-revision-mismatch')
                return
            except Exception:
                if attempt == 14:
                    raise ReleaseFailure('local-http-verification-failed') from None
                time.sleep(1)

    def resume_health(self):
        require(self.health_state is not None, 'health-prior-state-missing')
        for unit, state in self.health_state.items():
            if state == 'active':
                self.run(['systemctl', 'start', unit])

    def record(self, name, report):
        self.save(name + '.json', report)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or not all(valid_revision(value) for value in argv) or argv[0] == argv[1]:
        print(json.dumps({'published': False, 'failure': 'two-distinct-40hex-revisions-required'}))
        return 2
    os.umask(0o077)
    try:
        with publication_lock():
            result = publish_release(DockerHost(argv[0]), argv[0], argv[1])
    except BaseException:
        print(json.dumps({'published': False, 'failure': 'publisher-lock-unavailable'}))
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['published'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
