"""Server-side isolated validation. Never mounts live application data."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time

revision, expected_hash = sys.argv[1:]
assert re.fullmatch('[0-9a-f]{40}', revision) and re.fullmatch('[0-9a-f]{64}', expected_hash)
root = Path('/opt/spug')
archive = root / ('operations-' + revision[:7] + '.tar.gz')
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected_hash
stage = root / ('operations-' + revision[:7])
assert not stage.exists()
with tarfile.open(archive) as source:
    for member in source.getmembers():
        path = Path(member.name)
        assert not path.is_absolute() and '..' not in path.parts
        assert path.parts[0] == stage.name and (member.isfile() or member.isdir())
    source.extractall(root, filter='data')
image = 'spug-baota:' + revision[:7]
subprocess.run(['docker', 'build', '--network', 'none', '--build-arg', 'SOURCE_REVISION=' + revision,
                '-t', image, str(stage)], check=True)
subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', str(stage / 'source/deploy/platform-health/tests'), '-q'], check=True)
base = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
        '--memory', '256m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'PYTHONUNBUFFERED=1', '-v', str(stage / 'source/spug_api') + ':/source/spug_api:ro',
        '--workdir', '/source/spug_api', '--entrypoint', 'python3', image]
modules = ['apps.baota.' + p.stem for p in sorted((stage / 'source/spug_api/apps/baota').glob('test*.py'))]
subprocess.run(base + ['manage.py', 'test', *modules, 'apps.ops.tests', 'apps.setting.test_health',
                       '--settings=spug.test_settings', '--noinput'], check=True)
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
                '-v', str(stage / 'source/deploy/baota/test_image.py') + ':/test_image.py:ro',
                '--entrypoint', 'python3', image, '/test_image.py', 'worker'], check=True)
(stage / 'rollback.Dockerfile').write_text('''FROM spug-baota:a8af512
RUN test "$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)" -eq 1 && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh
COPY source/spug_api/apps/baota/models.py /opt/spug/spug_api/apps/baota/models.py
''')
rollback_image = 'spug-baota:operations-rollback-' + revision[:7]
subprocess.run(['docker', 'build', '--network', 'none', '-f', str(stage / 'rollback.Dockerfile'),
                '-t', rollback_image, str(stage)], check=True)
check_rollback = "from pathlib import Path; import configparser; p=Path('/entrypoint.sh').read_text(); assert 'updatedb' not in p and 'makemigrations' not in p; assert p.count('python3 manage.py migrate --noinput')==1; assert 'execution_policy = models.JSONField' in Path('/opt/spug/spug_api/apps/baota/models.py').read_text(); c=configparser.ConfigParser(); c.read('/etc/supervisord.conf'); assert len([n for n in c if n.startswith('program:')])==8; print('Schema-compatible eight-service rollback image verified.')"
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '64m',
                '--entrypoint', 'python3', rollback_image, '-c', check_rollback], check=True)

# Exact live migration files contain schema history only; no settings or rows.
# Rehearse applying that history, then generate/apply this release's additions.
rehearsal = stage / 'migration-rehearsal'
shutil.copytree(stage / 'source/spug_api', rehearsal)
export = """import io,sys,tarfile
from pathlib import Path
root=Path('/data/spug/spug_api')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as output:
 for folder in (root/'apps').glob('*/migrations'):
  for source in folder.glob('*.py'):
   output.add(source,arcname=source.relative_to(root).as_posix())
"""
migrations = subprocess.check_output(['docker', 'exec', 'spug', 'python3', '-c', export])
import io
with tarfile.open(fileobj=io.BytesIO(migrations)) as stream:
    for member in stream.getmembers():
        path = Path(member.name)
        assert not path.is_absolute() and '..' not in path.parts and member.isfile()
        assert len(path.parts) == 4 and path.parts[0] == 'apps' and path.parts[2] == 'migrations' and path.suffix == '.py'
    stream.extractall(rehearsal, filter='data')
network = 'ops-rehearsal-' + revision[:7]
db = 'baota-ci-db'
subprocess.run(['docker', 'network', 'create', '--internal', network], check=True, stdout=subprocess.DEVNULL)
try:
    subprocess.run(['docker', 'run', '-d', '--name', db, '--network', network, '--memory', '256m', '--cpus', '0.5',
                    '--pids-limit', '80', '-e', 'MARIADB_ROOT_PASSWORD=disposable-ops-test',
                    '-e', 'MARIADB_DATABASE=baota_ci', 'public.ecr.aws/docker/library/mariadb:10.11',
                    '--innodb-buffer-pool-size=64M', '--performance-schema=OFF', '--max-connections=20'],
                   check=True, stdout=subprocess.DEVNULL)
    for attempt in range(45):
        if subprocess.run(['docker', 'exec', db, 'healthcheck.sh', '--connect', '--innodb_initialized'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Disposable migration database not ready')
    script = rehearsal / 'ops_upgrade_check.py'
    script.write_text("""import os
os.environ['DJANGO_SETTINGS_MODULE']='spug.baota_ci_settings'
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
call_command('migrate', interactive=False, verbosity=0)
state=MigrationExecutor(connection).loader.project_state()
OldSetting=state.apps.get_model('setting','Setting')
before=OldSetting.objects.create(key='ops-release-sentinel',value='preserve-existing-row')
call_command('makemigrations','baota','ops',interactive=False,verbosity=1)
call_command('migrate',interactive=False,verbosity=0)
from apps.setting.models import Setting
from apps.ops.models import OpsRun,OpsTarget,OpsBaseline
assert Setting.objects.get(pk=before.pk).value=='preserve-existing-row'
assert not OpsRun.objects.exists() and not OpsTarget.objects.exists() and not OpsBaseline.objects.exists()
call_command('makemigrations','baota','ops',check=True,dry_run=True,interactive=False,verbosity=0)
print('Exact live migration history upgraded; sentinel preserved; no pending model changes.')
""")
    mysql = ['docker', 'run', '--rm', '--network', network, '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
             '--memory', '256m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
             '-e', 'BAOTA_CI_DB_HOST=baota-ci-db', '-e', 'BAOTA_CI_DB_PASSWORD=disposable-ops-test',
             '-v', str(rehearsal) + ':/source/spug_api:rw', '--workdir', '/source/spug_api', '--entrypoint', 'python3', image]
    subprocess.run(mysql + ['ops_upgrade_check.py'], check=True)
    subprocess.run(mysql + ['manage.py', 'test', 'apps.baota.test_batches', 'apps.baota.test_tasks',
                           'apps.baota.test_upload_tasks', 'apps.ops.tests',
                           '--settings=spug.baota_ci_settings', '--noinput'], check=True)
finally:
    subprocess.run(['docker', 'rm', '-fv', db], stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'network', 'rm', network], stdout=subprocess.DEVNULL)
(stage / 'isolated-checks.json').write_text(json.dumps({'revision': revision, 'image': image,
    'sqlite_regression': True, 'mariadb_migration_history_upgrade': True, 'mariadb_transactions': True}))
print(json.dumps({'revision': revision, 'isolated_checks': 'passed', 'stage': str(stage)}))
