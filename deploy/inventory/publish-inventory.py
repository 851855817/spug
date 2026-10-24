"""Controlled hot release: paired backup, additive migration, preserved queues."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from release_safety import write_database_backup

revision = sys.argv[1]
assert re.fullmatch('[0-9a-f]{40}', revision)
root = Path('/opt/spug')
stage = root / ('inventory-' + revision[:7])
assert json.loads((stage / 'isolated-checks.json').read_text())['revision'] == revision
image = 'spug-baota:' + revision[:7]
compose = root / 'docker-compose.yml'
previous_compose = compose.read_text()
assert previous_compose.count('image: spug-baota:2a2eb36') == 1

def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=120, **kwargs).strip()

def control(action, *services):
    return run('docker', 'exec', 'spug', 'supervisorctl', action, *services)

preserve = (root / 'preserve-monitor-manual.py').read_text()
# Freeze all existing operations history too. Ignore only newly introduced
# inventory tables, which must be empty until administrators explicitly collect.
assert preserve.count("if model.__name__ not in ('ResourceMonitorSnapshot', 'ResourceMonitorLease')") == 1
assert preserve.count('models += [Host') == 1
assert preserve.count('    assert not PanelLock.objects.exists()') == 1
preserve = preserve.replace("if model.__name__ not in ('ResourceMonitorSnapshot', 'ResourceMonitorLease')",
                            "if model.__name__ not in ('ResourceMonitorSnapshot', 'ResourceMonitorLease', 'PanelInventorySnapshot', 'PanelInventoryLease')")
preserve = preserve.replace('models += [Host', "models += list(apps.get_app_config('ops').get_models())\nmodels += [Host")
preserve = preserve.replace("    assert not PanelLock.objects.exists()", "    from apps.ops.models import OpsRun, OpsTarget\n    assert not OpsRun.objects.filter(state__in=('queued','running')).exists(), 'Active operations job; defer release'\n    assert not OpsTarget.objects.filter(state='running').exists(), 'Operations target running'\n    assert not PanelLock.objects.exists()")


def fingerprint(idle=False):
    process = subprocess.run(['docker', 'exec', '-i', 'spug', 'python3', '-'] + (['--require-idle'] if idle else []),
                             input=preserve, text=True, capture_output=True, timeout=90)
    if process.returncode:
        raise RuntimeError('Business preservation or idle check failed; no raw data printed')
    return json.loads(process.stdout)

before = fingerprint(True)
with urllib.request.urlopen('http://127.0.0.1:8080/ui-release.json', timeout=5) as response:
    previous_ui_revision = json.load(response)['revision']
workers = ('spug-worker', 'spug-monitor')
worker_pids = {n: control('pid', n) for n in workers}
container = run('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', 'spug')
backup = root / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-before-inventory-' + revision[:7])
backup.mkdir(mode=0o700, exist_ok=False)

def save(name, data):
    destination = backup / name
    destination.write_text(json.dumps(data, indent=2))
    destination.chmod(0o600)

shutil.copy2(compose, backup / 'docker-compose.yml')
shutil.copy2(root / 'platform-health/spug-health.py', backup / 'spug-health.py')
save('preservation-before.json', before)
run('docker', 'cp', 'spug:/etc/supervisord.conf', str(backup / 'supervisord.conf'))
configuration = (backup / 'supervisord.conf').read_text()
assert configuration.count('[program:spug-ops-worker]') == 1
maintenance = ('nginx', 'spug-api', 'spug-ws', 'spug-panel-worker', 'spug-ops-worker', 'spug-scheduler')
dest = '/tmp/spug-ui-' + revision[:7]
run('docker', 'exec', 'spug', 'mkdir', '-p', dest)
for name in ('build', 'source', 'api-release.json', 'apply-operations.py', 'deploy-compact-ui.py'):
    run('docker', 'cp', str(stage / name), 'spug:' + dest + '/' + name)

overlay = ui_published = migration_started = safe_to_resume_health = False
subprocess.run(['systemctl', 'stop', 'spug-platform-health.timer', 'spug-platform-health.service'], check=True)
try:
    print(control('stop', *maintenance), flush=True)
    assert fingerprint(True) == before, 'Business changed while entering maintenance'
    # Back up the configured application database. DB root uses a random
    # password; never print credentials or place them on a command line.
    db_read = "import os,sys,json; sys.path.insert(0,'/data/spug/spug_api'); os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'; import django; django.setup(); from django.conf import settings; d=settings.DATABASES['default']; print(json.dumps({k:d[k] for k in ('NAME','USER','PASSWORD')}))"
    credentials = json.loads(run('docker', 'exec', 'spug', 'python3', '-c', db_read))
    assert re.fullmatch('[A-Za-z0-9_]+', credentials['NAME'])
    def quote_option(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
    option_file = '/tmp/ops-backup-' + revision[:7] + '.cnf'
    options = '[client]\nuser=' + quote_option(credentials['USER']) + '\npassword=' + quote_option(credentials['PASSWORD']) + '\n'
    subprocess.run(['docker', 'exec', '-i', 'spug-db', 'sh', '-c',
                    'umask 077; set -C; cat > ' + option_file], input=options, text=True, check=True)
    try:
        dump = ['docker', 'exec', 'spug-db', 'mariadb-dump', '--defaults-extra-file=' + option_file,
                '--single-transaction', '--routines', '--events', '--databases', credentials['NAME']]
        write_database_backup(dump, backup)
    finally:
        subprocess.run(['docker', 'exec', 'spug-db', 'rm', '-f', option_file], check=True)
        del credentials, options
    with (backup / 'spug-data.tar.gz').open('wb') as output:
        subprocess.run(['docker', 'exec', 'spug', 'tar', '-C', '/data', '-czf', '-', '.'],
                       stdout=output, check=True, timeout=180)
    lines = []
    for name in ('database.sql.gz', 'spug-data.tar.gz'):
        path = backup / name
        path.chmod(0o600)
        assert path.stat().st_size > 100
        digest_state = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest_state.update(block)
        digest = digest_state.hexdigest()
        lines.append(digest + '  ' + name)
    (backup / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
    (backup / 'SHA256SUMS').chmod(0o600)
    subprocess.run(['gzip', '-t', str(backup / 'database.sql.gz')], check=True)
    subprocess.run(['tar', '-tzf', str(backup / 'spug-data.tar.gz')], check=True, stdout=subprocess.DEVNULL)
    print(run('docker', 'exec', 'spug', 'python3', dest + '/apply-operations.py', revision, 'publish'), flush=True)
    overlay = True
    for args in (('makemigrations', 'baota', 'ops', '--noinput'), ('migrate', '--noinput'),
                 ('makemigrations', 'baota', 'ops', '--check', '--dry-run', '--noinput')):
        if args[0] == 'migrate':
            migration_started = True
        print(run('docker', 'exec', '-w', '/data/spug/spug_api', 'spug', 'python3', 'manage.py', *args), flush=True)
    print(run('docker', 'exec', 'spug', 'python3', dest + '/deploy-compact-ui.py', revision), flush=True)
    ui_published = True
    # Nine-service supervisor wiring is unchanged. No new background collector.
    # Verify preservation before opening ingress; no test writes to live tables.
    after = fingerprint()
    save('preservation-after.json', after)
    assert before == after, 'Business fields or key digests changed'
    inventory_check = """import os,sys
sys.path.insert(0,'/data/spug/spug_api')
os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'
import django
django.setup()
from apps.account.models import User
from apps.baota.inventory.models import PanelInventorySnapshot,PanelInventoryLease
from apps.baota.inventory import service
from unittest.mock import patch
assert not PanelInventorySnapshot.objects.exists() and not PanelInventoryLease.objects.exists()
admin=User.objects.filter(is_supper=True,is_active=True,is_deleted=False).first()
assert admin is not None
with patch.object(service,'collect_panel',side_effect=AssertionError('Unexpected collection')):
 service.options(admin)
 for kind in ('sites','backups'):
  result=service.listing(admin,kind,{})
  assert not result['items'] and result['total']==0
assert not PanelInventorySnapshot.objects.exists() and not PanelInventoryLease.objects.exists()
print('Inventory empty; cache-only reads did not start collection or create rows.')
"""
    print(run('docker', 'exec', 'spug', 'python3', '-c', inventory_check), flush=True)
    assert worker_pids == {n: control('pid', n) for n in workers}, 'Legacy worker PID changed'
    assert container == run('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', 'spug')
    compose.write_text(previous_compose.replace('image: spug-baota:2a2eb36', 'image: ' + image))
    compose.chmod(0o600)
    subprocess.run(['docker', 'compose', '-f', str(compose), 'config', '--quiet'], check=True)
    print(control('start', 'spug-panel-worker', 'spug-ops-worker', 'spug-scheduler', 'spug-api', 'spug-ws', 'nginx'), flush=True)
    services = control('status').splitlines()
    assert len(services) == 9 and all('RUNNING' in line for line in services)
    for attempt in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/api/account/login/', timeout=5) as response:
                assert response.status == 200
            break
        except Exception:
            if attempt == 14:
                raise
            time.sleep(1)
    with urllib.request.urlopen('http://127.0.0.1:8080/ui-release.json', timeout=5) as response:
        assert json.load(response)['revision'] == revision
    save('publication.json', {'revision': revision, 'future_recreate_image': image, 'services_running': 9,
                             'http': 200, 'data_and_keys_unchanged': True, 'legacy_workers_unchanged': True,
                             'same_container': True, 'backup': str(backup)})
    print(json.dumps({'revision': revision, 'published': True, 'backup': str(backup)}), flush=True)
    safe_to_resume_health = True
except Exception:
    # Keep additive schema and compatibility model; never generate reverse DDL.
    rollback_errors = []
    def attempt(name, *args):
        try:
            run(*args)
            return True
        except Exception:
            rollback_errors.append(name)
            return False
    subprocess.run(['docker', 'exec', 'spug', 'supervisorctl', 'stop', *maintenance],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=120)
    column_check = """import os,sys
sys.path.insert(0,'/data/spug/spug_api')
os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'
import django
django.setup()
from django.db import connection
from apps.baota.inventory.models import PanelInventorySnapshot,PanelInventoryLease
models=(PanelInventorySnapshot,PanelInventoryLease)
tables=set(connection.introspection.table_names())
present=[model._meta.db_table in tables for model in models]
if not any(present):
 print('0')
else:
 assert all(present), 'Partial inventory migration; keep ingress closed'
 with connection.cursor() as cursor:
  for model in models:
   columns={c.name for c in connection.introspection.get_table_description(cursor,model._meta.db_table)}
   assert {f.column for f in model._meta.local_fields} <= columns
 print('1')
"""
    schema_added = False
    if migration_started:
        try:
            schema_added = run('docker', 'exec', 'spug', 'python3', '-c', column_check) == '1'
        except Exception:
            schema_added = None
            rollback_errors.append('schema-verification-required-before-service-start')
    if overlay:
        attempt('api-restore', 'docker', 'exec', 'spug', 'python3', dest + '/apply-operations.py', revision, 'restore')
        if schema_added is not False:
            for folder in ('/opt/spug/spug_api', '/data/spug/spug_api'):
                attempt('compat-model', 'docker', 'exec', 'spug', 'cp', dest + '/source/spug_api/apps/baota/models.py', folder + '/apps/baota/models.py')
                attempt('compat-inventory', 'docker', 'exec', 'spug', 'cp', '-a', dest + '/source/spug_api/apps/baota/inventory', folder + '/apps/baota/')
    if ui_published:
        script = "from pathlib import Path; import shutil; revision='" + revision + "'; roots=['/opt/spug/spug_web/build','/data/spug/spug_web/build']; saved=Path('/data/spug-ui-backups')/revision; [(shutil.copyfile(saved/str(i)/n,Path(p)/n)) for i,p in enumerate(roots) for n in ('index.html','asset-manifest.json','ui-release.json') if (saved/str(i)/n).is_file()]"
        attempt('ui-restore', 'docker', 'exec', 'spug', 'python3', '-c', script)
    shutil.copyfile(backup / 'spug-health.py', root / 'platform-health/spug-health.py')
    rollback_compose = previous_compose.replace('image: spug-baota:2a2eb36',
        'image: spug-baota:inventory-rollback-' + revision[:7]) if schema_added is not False else previous_compose
    compose.write_text(rollback_compose)
    compose.chmod(0o600)
    if schema_added is not None and not rollback_errors:
        try:
            assert fingerprint() == before, 'Rollback business preservation mismatch'
            assert container == run('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', 'spug')
            assert worker_pids == {n: control('pid', n) for n in workers}
            subprocess.run(['docker', 'compose', '-f', str(compose), 'config', '--quiet'], check=True)
            control('start', 'spug-panel-worker', 'spug-ops-worker', 'spug-scheduler', 'spug-api', 'spug-ws', 'nginx')
            services = control('status').splitlines()
            assert len(services) == 9 and all('RUNNING' in line for line in services)
            with urllib.request.urlopen('http://127.0.0.1:8080/ui-release.json', timeout=5) as response:
                assert json.load(response)['revision'] == previous_ui_revision
            with urllib.request.urlopen('http://127.0.0.1:8080/api/account/login/', timeout=5) as response:
                assert response.status == 200
            safe_to_resume_health = True
        except Exception:
            rollback_errors.append('rollback-verification-failed-ingress-kept-closed')
            subprocess.run(['docker', 'exec', 'spug', 'supervisorctl', 'stop', *maintenance],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=120)
    save('rollback.json', {'schema_retained': schema_added, 'errors': rollback_errors})
    print(json.dumps({'rolled_back': not rollback_errors, 'rollback_errors': rollback_errors}), flush=True)
    raise
finally:
    if safe_to_resume_health:
        subprocess.run(['systemctl', 'start', 'spug-platform-health.timer'], check=True)
        subprocess.run(['systemctl', 'start', 'spug-platform-health.service'], check=True)
    else:
        print('Recovery is unverified; ingress and automatic health recovery remain stopped. Manual review required.', flush=True)
