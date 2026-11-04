"""Server-side isolated validation and live-history additive migration rehearsal."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv, **kwargs):
    return subprocess.check_output(argv, text=True, timeout=kwargs.pop('timeout', 600), **kwargs).strip()


def hashes(root):
    assert not any(p.is_symlink() for p in root.rglob('*'))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def main():
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision')
    parser.add_argument('archive_sha256')
    options = parser.parse_args()
    revision = options.revision
    assert re.fullmatch('[0-9a-f]{40}', revision) and re.fullmatch('[0-9a-f]{64}', options.archive_sha256)
    root = Path('/opt/spug')
    archive = root / ('ssl-' + revision[:7] + '.tar.gz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == options.archive_sha256
    # Bootstrap helper is the exact reviewed file extracted from the archive.
    with tarfile.open(archive) as stream:
        source_name = 'ssl-' + revision[:7] + '/source/deploy/upgrade-review/prepare.py'
        content = stream.extractfile(source_name).read()
    temporary = root / ('ssl-extract-helper-' + revision[:7] + '.py')
    with temporary.open('xb') as output:
        temporary.chmod(0o600)
        output.write(content)
    old_prepare = load('ssl_base_prepare', temporary)
    stage = root / ('ssl-' + revision[:7])
    old_prepare.safe_extract(archive, root, stage.name)
    original = hashes(stage)
    release = json.loads((stage / 'release.json').read_text())
    assert release['revision'] == revision and release['schema_changes'] is True
    previous = release['previous_revision']
    assert re.fullmatch('[0-9a-f]{40}', previous)
    prepared = root / ('ssl-prepared-' + revision[:7])
    shutil.copytree(stage, prepared)
    utilities = prepared / 'source/deploy/ssl-monitor'
    sys.path.insert(0, str(utilities))
    from manifest import API_FILES, COMPAT_FILES
    from contract import HELPER_SOURCES, derived_release, dockerfiles, validate_migration
    assert set(release['api_sha256']) == set(API_FILES)
    assert all(hashes(prepared / 'source/spug_api').get(name) == value for name, value in release['api_sha256'].items())
    # Export only migration Python files. No production database rows, settings,
    # credentials or volumes are mounted into any test container.
    export = '''import sys,tarfile
from pathlib import Path
root=Path('/data/spug/spug_api')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as out:
 for folder in (root/'apps').glob('*/migrations'):
  for path in folder.glob('*.py'):
   assert path.is_file() and not path.is_symlink() and path.stat().st_size < 1048576
   out.add(path,arcname=path.relative_to(root).as_posix())
'''
    data = subprocess.check_output(['docker', 'exec', 'spug', 'python3', '-c', export], timeout=90)
    assert len(data) < 10 * 1024**2
    rehearsal = root / ('ssl-rehearsal-' + revision[:7])
    shutil.copytree(prepared / 'source/spug_api', rehearsal)
    with tarfile.open(fileobj=io.BytesIO(data)) as stream:
        for member in stream.getmembers():
            path = Path(member.name)
            assert member.isfile() and len(path.parts) == 4 and path.parts[0] == 'apps'
            assert path.parts[2] == 'migrations' and path.suffix == '.py' and '..' not in path.parts
        stream.extractall(rehearsal, filter='data')
    image_base = 'spug-baota:' + previous[:7]
    image = 'spug-baota:' + revision[:7]
    base_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_base])
    # The existing test settings deliberately accept only this disposable name.
    # Refuse an occupied name rather than deleting another task's container.
    network, database = 'ssl-rehearsal-' + revision[:7], 'baota-ci-db'
    assert not run(['docker', 'ps', '-aq', '--filter', 'name=^/' + database + '$'])
    run(['docker', 'network', 'create', '--internal', network])
    started = False
    try:
        run(['docker', 'run', '-d', '--name', database, '--network', network, '--memory', '256m',
             '--cpus', '0.5', '--pids-limit', '80', '-e', 'MARIADB_ROOT_PASSWORD=disposable-ssl-fixture',
             '-e', 'MARIADB_DATABASE=baota_ci', 'public.ecr.aws/docker/library/mariadb:10.11',
             '--innodb-buffer-pool-size=64M', '--performance-schema=OFF', '--max-connections=20'])
        started = True
        for _ in range(45):
            if subprocess.run(['docker', 'exec', database, 'healthcheck.sh', '--connect', '--innodb_initialized'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Disposable database did not become ready')
        base = ['docker', 'run', '--rm', '--network', network, '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
            '--memory', '384m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'BAOTA_CI_DB_HOST=' + database, '-e', 'BAOTA_CI_DB_PASSWORD=disposable-ssl-fixture',
            '-v', str(rehearsal) + ':/source:rw', '-v', str(utilities) + ':/utilities:ro',
            '--workdir', '/source', '--entrypoint', 'python3', image_base]
        # Result is copied through an explicit stdout JSON read, not a live mount.
        runner = "import runpy;runpy.run_path('/utilities/rehearsal.py',run_name='__main__');print(open('/tmp/ssl-rehearsal.json').read())"
        result = run(base + ['-c', "import sys;sys.path.insert(0,'/utilities');" + runner])
        proof = json.loads(result.splitlines()[-1])
        migration = proof['migration']
        assert re.fullmatch(r'[0-9]{4}_ssl_monitor', migration) and proof['append_only'] is True
        migration_name = 'apps/monitor/migrations/' + migration + '.py'
        target = prepared / 'source/spug_api' / migration_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rehearsal / migration_name, target)
        validate_migration(target.read_text())
        release = derived_release(release, migration, hashlib.sha256(target.read_bytes()).hexdigest())
        (prepared / 'api-release.json').write_text(json.dumps({'revision': revision,
            'migration': migration, 'sha256': release['api_sha256']}, indent=2))
        (prepared / 'release.json').write_text(json.dumps(release, indent=2))
        for name, origin in HELPER_SOURCES.items():
            shutil.copy2(prepared / origin, prepared / name)
        candidate_dockerfile, rollback_dockerfile = dockerfiles(previous, migration)
        (prepared / 'Dockerfile').write_text(candidate_dockerfile)
        run(['docker', 'build', '--network', 'none', '--build-arg', 'SOURCE_REVISION=' + revision,
             '-t', image, str(prepared)])
        rollback = 'spug-baota:ssl-rollback-' + revision[:7]
        (prepared / 'rollback.Dockerfile').write_text(rollback_dockerfile)
        run(['docker', 'build', '--network', 'none', '-f', str(prepared / 'rollback.Dockerfile'),
             '--build-arg', 'SOURCE_REVISION=' + previous, '-t', rollback, str(prepared)])
        # Rehearse the compatibility source against the upgraded disposable DB.
        compatibility = root / ('ssl-compat-rehearsal-' + revision[:7])
        compatibility.mkdir(exist_ok=False)
        exported = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--memory', '128m', '--entrypoint', 'tar', rollback, '-C', '/opt/spug/spug_api', '-cf', '-', '.'], timeout=90)
        with tarfile.open(fileobj=io.BytesIO(exported)) as stream:
            for member in stream.getmembers():
                path = Path(member.name)
                assert not path.is_absolute() and '..' not in path.parts and (member.isfile() or member.isdir())
            stream.extractall(compatibility, filter='data')
        # Existing /data migration history survives upstream rsync on restart;
        # reproduce that same merge using migration Python only, never settings.
        for folder in (rehearsal / 'apps').glob('*/migrations'):
            target_folder = compatibility / folder.relative_to(rehearsal)
            target_folder.mkdir(parents=True, exist_ok=True)
            for item in folder.glob('*.py'):
                shutil.copy2(item, target_folder / item.name)
        compat_base = list(base)
        compat_base[compat_base.index(str(rehearsal) + ':/source:rw')] = str(compatibility) + ':/source:rw'
        compat_base[-1] = rollback
        check = "import os;os.environ['DJANGO_SETTINGS_MODULE']='spug.baota_ci_settings';import django;django.setup();from django.core.management import call_command;call_command('migrate',interactive=False,verbosity=0);call_command('makemigrations',check=True,dry_run=True,interactive=False,verbosity=0);from django.apps import apps;from apps.setting.models import Setting;assert Setting.objects.get(key='ssl-release-sentinel').value=='preserve-existing-nonempty-data';assert len([m for m in apps.get_models() if m._meta.db_table.startswith('monitor_ssl_')])==6;print('Compatibility startup, migration history and nonempty sentinel verified.')"
        run(compat_base + ['-c', check])
        wiring = "from pathlib import Path;import configparser;p=Path('/entrypoint.sh').read_text();assert 'updatedb' not in p and 'makemigrations' not in p and p.count('python3 manage.py migrate --noinput')==1;c=configparser.ConfigParser();c.read('/etc/supervisord.conf');assert len([n for n in c if n.startswith('program:')])==9;assert 'ssl.models import' in Path('/opt/spug/spug_api/apps/monitor/models.py').read_text();print('Nine-service compatible image startup verified.')"
        for restart_image in (image, rollback):
            run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
                 '--entrypoint', 'python3', restart_image, '-c', wiring])
        # Transaction/recovery tests use the disposable DB and mocked transports.
        run(base + ['manage.py', 'test', 'apps.monitor.ssl.tests',
                    '--settings=spug.baota_ci_settings', '--noinput'])
    finally:
        if started: run(['docker', 'rm', '-fv', database])
        run(['docker', 'network', 'rm', network])
    linux = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
        '--memory', '384m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-v', str(prepared / 'source') + ':/source:ro', '--workdir', '/source/spug_api', '--entrypoint', 'python3', image]
    modules = ['apps.baota.' + p.stem for p in sorted((prepared / 'source/spug_api/apps/baota').glob('test*.py'))]
    run(linux + ['manage.py', 'test', *modules, 'apps.ops.tests', 'apps.ops.test_result_counts', 'apps.ops.expiry.tests',
        'apps.baota.inventory.tests', 'apps.schedule.test_panel_tasks', 'apps.schedule.test_urls',
        'apps.setting.test_health', 'apps.setting.test_reminder_preferences', 'apps.setting.test_appearance',
        'apps.monitor.ssl.tests', 'apps.monitor.ssl.test_collectors', '--settings=spug.test_settings', '--noinput'])
    for folder in ('platform-health/tests', 'inventory', 'expiry', 'upgrade-review', 'ssl-monitor'):
        run(['python3', '-m', 'unittest', 'discover', '-s', str(prepared / 'source/deploy' / folder), '-p', 'test_*.py', '-q'])
    base_publish = load('ssl_base_publish', prepared / 'source/deploy/upgrade-review/publish.py')
    old_image = old_prepare.image_api_snapshot(base_id, base_publish.FILE_PROBE)
    image_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
    candidate_image = old_prepare.image_api_snapshot(image_id, base_publish.FILE_PROBE)
    assert candidate_image == dict(old_image, **release['api_sha256'])
    assert all(old_image.get(name) == value for name, value in release['previous_api_sha256'].items())
    rollback_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', rollback])
    rollback_image = old_prepare.image_api_snapshot(rollback_id, base_publish.FILE_PROBE)
    assert rollback_image == dict(old_image, **{name: release['api_sha256'][name]
        for name in (*COMPAT_FILES, migration_name)})
    checks = {'revision': revision, 'previous_revision': previous, 'archive_sha256': options.archive_sha256,
        'schema_changes': True, 'linux_regression': True, 'mariadb_regression': True, 'migration_rehearsal': True,
        'rollback_rehearsal': True, 'release_failure_tests': True, 'image_tree_exact': True,
        'image_id': image_id, 'baseline_image_id': base_id,
        'rollback_image_id': rollback_id, 'rollback_image_tree_exact': True,
        'migration': release['migration'], 'new_schema': proof['new_schema'],
        'original_files': original, 'prepared_files': hashes(prepared)}
    (stage / 'isolated-checks.json').write_text(json.dumps(checks, indent=2))
    print(json.dumps({'revision': revision, 'isolated_checks': True, 'migration': release['migration']}))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'isolated_checks': False, 'failure': 'ssl-isolated-validation-failed'}))
        sys.exit(1)
