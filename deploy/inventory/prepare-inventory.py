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
archive = root / ('inventory-' + revision[:7] + '.tar.gz')
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected_hash
stage = root / ('inventory-' + revision[:7])
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
subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', str(stage / 'source/deploy/inventory'), '-p', 'test_*.py', '-q'], check=True)
base = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
        '--memory', '256m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'PYTHONUNBUFFERED=1', '-v', str(stage / 'source/spug_api') + ':/source/spug_api:ro',
        '--workdir', '/source/spug_api', '--entrypoint', 'python3', image]
modules = ['apps.baota.' + p.stem for p in sorted((stage / 'source/spug_api/apps/baota').glob('test*.py'))]
subprocess.run(base + ['manage.py', 'test', *modules, 'apps.ops.tests', 'apps.ops.test_result_counts', 'apps.baota.inventory.tests', 'apps.setting.test_health',
                       '--settings=spug.test_settings', '--noinput'], check=True)
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
                '-v', str(stage / 'source/deploy/baota/test_image.py') + ':/test_image.py:ro',
                '--entrypoint', 'python3', image, '/test_image.py', 'worker'], check=True)
(stage / 'rollback.Dockerfile').write_text('''FROM spug-baota:2a2eb36
RUN test "$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)" -eq 1 && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh
COPY source/spug_api/apps/baota/models.py /opt/spug/spug_api/apps/baota/models.py
COPY source/spug_api/apps/baota/inventory/ /opt/spug/spug_api/apps/baota/inventory/
''')
rollback_image = 'spug-baota:inventory-rollback-' + revision[:7]
subprocess.run(['docker', 'build', '--network', 'none', '-f', str(stage / 'rollback.Dockerfile'),
                '-t', rollback_image, str(stage)], check=True)
check_rollback = "from pathlib import Path; import configparser; p=Path('/entrypoint.sh').read_text(); assert 'updatedb' not in p and 'makemigrations' not in p; assert p.count('python3 manage.py migrate --noinput')==1; assert 'execution_policy = models.JSONField' in Path('/opt/spug/spug_api/apps/baota/models.py').read_text(); c=configparser.ConfigParser(); c.read('/etc/supervisord.conf'); assert len([n for n in c if n.startswith('program:')])==9; print('Schema-compatible nine-service rollback image verified.')"
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
network = 'inventory-rehearsal-' + revision[:7]
db = 'baota-ci-db'
existing = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=^/baota-ci-db$'], text=True).strip()
assert not existing, 'Disposable database name is already occupied; refusing to remove another container'
db_started = False
subprocess.run(['docker', 'network', 'create', '--internal', network], check=True, stdout=subprocess.DEVNULL)
try:
    subprocess.run(['docker', 'run', '-d', '--name', db, '--network', network, '--memory', '256m', '--cpus', '0.5',
                    '--pids-limit', '80', '-e', 'MARIADB_ROOT_PASSWORD=disposable-ops-test',
                    '-e', 'MARIADB_DATABASE=baota_ci', 'public.ecr.aws/docker/library/mariadb:10.11',
                    '--innodb-buffer-pool-size=64M', '--performance-schema=OFF', '--max-connections=20'],
                   check=True, stdout=subprocess.DEVNULL)
    db_started = True
    for attempt in range(45):
        if subprocess.run(['docker', 'exec', db, 'healthcheck.sh', '--connect', '--innodb_initialized'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Disposable migration database not ready')
    script = rehearsal / 'ops_upgrade_check.py'
    script.write_text("""import os,json
os.environ['DJANGO_SETTINGS_MODULE']='spug.baota_ci_settings'
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
call_command('migrate', interactive=False, verbosity=0)
state=MigrationExecutor(connection).loader.project_state()
OldSetting=state.apps.get_model('setting','Setting')
before=OldSetting.objects.create(key='inventory-release-sentinel',value='preserve-existing-row')
OldUser=state.apps.get_model('account','User')
actor=OldUser.objects.create(username='disposable-upgrade-fixture',is_supper=True)
old_models=[state.apps.get_model('ops',name) for name in ('OpsRun','OpsTarget','OpsBaseline','OpsAudit')]
OldRun,OldTarget,OldBaseline,OldAudit=old_models
job=OldRun.objects.create(job='linux_basic',job_name='Migration fixture',version='1',state='success',created_by=actor)
target=OldTarget.objects.create(run=job,position=0,identity_digest='a'*64,state='success',
 checks=[{'key':'fixture','status':'warning','value':1}])
OldBaseline.objects.create(name='Preserve baseline',job=job.job,version='1',checks=target.checks,target=target,created_by=actor)
OldAudit.objects.create(run=job,event='fixture',actor_id=actor.pk,detail={'preserve':True})
frozen=json.dumps([list(model.objects.order_by('pk').values()) for model in old_models],sort_keys=True,default=str)
call_command('makemigrations','baota','ops',interactive=False,verbosity=1)
call_command('migrate',interactive=False,verbosity=0)
from apps.setting.models import Setting
from apps.ops.models import OpsRun,OpsTarget,OpsBaseline,OpsAudit
assert Setting.objects.get(pk=before.pk).value=='preserve-existing-row'
assert frozen==json.dumps([list(model.objects.order_by('pk').values()) for model in
 (OpsRun,OpsTarget,OpsBaseline,OpsAudit)],sort_keys=True,default=str)
from apps.baota.inventory.models import PanelInventorySnapshot,PanelInventoryLease
assert not PanelInventorySnapshot.objects.exists() and not PanelInventoryLease.objects.exists()
call_command('makemigrations','baota','ops',check=True,dry_run=True,interactive=False,verbosity=0)
print('Exact live migration history upgraded; setting, operations, targets, baseline and audit preserved; no pending model changes.')
""")
    mysql = ['docker', 'run', '--rm', '--network', network, '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
             '--memory', '256m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
             '-e', 'BAOTA_CI_DB_HOST=baota-ci-db', '-e', 'BAOTA_CI_DB_PASSWORD=disposable-ops-test',
             '-v', str(rehearsal) + ':/source/spug_api:rw', '--workdir', '/source/spug_api', '--entrypoint', 'python3', image]
    subprocess.run(mysql + ['ops_upgrade_check.py'], check=True)
    subprocess.run(mysql + ['manage.py', 'test', 'apps.baota.test_batches', 'apps.baota.test_tasks',
                           'apps.baota.test_upload_tasks', 'apps.ops.tests', 'apps.ops.test_result_counts', 'apps.baota.inventory.tests',
                           '--settings=spug.baota_ci_settings', '--noinput'], check=True)
finally:
    if db_started:
        subprocess.run(['docker', 'rm', '-fv', db], stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'network', 'rm', network], stdout=subprocess.DEVNULL)
(stage / 'isolated-checks.json').write_text(json.dumps({'revision': revision, 'image': image,
    'sqlite_regression': True, 'mariadb_migration_history_upgrade': True, 'mariadb_transactions': True}))
print(json.dumps({'revision': revision, 'isolated_checks': 'passed', 'stage': str(stage)}))
