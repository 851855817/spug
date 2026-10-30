"""Read-only release preservation and idle guard. Emit hashes, never customer fields."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, '/data/spug/spug_api')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
import django
django.setup()
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from django_redis import get_redis_connection
from apps.baota.models import Panel, PanelLock, PanelTaskTarget
from apps.baota.inventory.models import PanelInventoryLease
from apps.ops.models import OpsRun, OpsTarget
from apps.exec.models import ExecHistory, Transfer
from apps.schedule.models import Task, History as ScheduleHistory
from apps.deploy.models import DeployRequest
from apps.repository.models import Repository
from apps.pipeline.models import PipeHistory
from apps.monitor.models import Detection


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()).hexdigest()


def read_schema(database):
    # Row exclusions for volatile cache never exclude their table structure.
    schema = {}
    with database.cursor() as cursor:
        for table in sorted(database.introspection.table_names()):
            schema[table] = {'columns': database.introspection.get_table_description(cursor, table),
                             'constraints': database.introspection.get_constraints(cursor, table)}
    return schema


def normalized_rows(model, rows, now):
    # Auth middleware renews this TTL on every authenticated GET. Preserve
    # identity/token/permissions and revocation; only an active TTL's exact
    # future second is volatile. No database value is modified.
    if (model._meta.app_label, model.__name__) != ('account', 'User'):
        return rows
    return [dict(row, token_expired='active-session-expiry')
            if (row.get('is_active') is True and isinstance(row.get('access_token'), str)
                and len(row['access_token']) == 32 and type(row.get('token_expired')) is int
                and row['token_expired'] >= now) else dict(row)
            for row in rows]


def failed_renewal_preflight(target):
    """A complete failed preview has no dispatchable child; preserve it as-is.

    Ready, missing, dispatched or reconciled history always blocks. All rows
    remain in the preservation digest, so a concurrent start still stops release.
    """
    if (not isinstance(target, dict) or target.get('state') != 'prepared'
            or target.get('reconcile_requested') is not False
            or target.get('task__state') != 'prepared' or target.get('task__operation') != 'ssl_renew'):
        return False
    audit = target.get('task__audit')
    # prepare() always records creation; it has not authorized a dispatch.
    if audit != [] and not (isinstance(audit, list) and len(audit) == 1
            and isinstance(audit[0], dict) and set(audit[0]) == {'action', 'actor_id', 'at'}
            and audit[0]['action'] == 'prepare' and type(audit[0]['actor_id']) is int
            and audit[0]['actor_id'] > 0 and isinstance(audit[0]['at'], str) and bool(audit[0]['at'])):
        return False
    children = target.get('children')
    return isinstance(children, list) and bool(children) and all(
        isinstance(child, dict) and child.get('state') == 'failed'
        and isinstance(child.get('snapshot'), dict) and child['snapshot'].get('state') == 'failed'
        and isinstance(child.get('result'), dict) and child['result'].get('state') == 'failed'
        and 'remote' not in child['result'] and child.get('intents') == [] and child.get('resolutions') == []
        for child in children)


models = [model for model in apps.get_models(include_auto_created=True)
          if (model._meta.app_label, model.__name__) not in (
              ('baota', 'ResourceMonitorSnapshot'), ('baota', 'ResourceMonitorLease'))]
tables = set(connection.introspection.table_names())
result = {}
for model in sorted(models, key=lambda value: value._meta.db_table):
    table = model._meta.db_table
    assert table in tables, 'Missing business schema; refuse release'
    rows = normalized_rows(model, list(model.objects.order_by('pk').values()), time.time())
    result[table] = {'count': len(rows), 'digest': digest(rows)}
with connection.cursor() as cursor:
    cursor.execute('SELECT app, name, applied FROM django_migrations ORDER BY app, name')
    result['migration_rows_sha256'] = digest(cursor.fetchall())
result['schema_sha256'] = digest(read_schema(connection))
executor = MigrationExecutor(connection)
assert not executor.migration_plan(executor.loader.graph.leaf_nodes()), 'Unapplied migration; refuse release'
for name in ('baota.key', 'baota.key.sha256'):
    result[name] = hashlib.sha256(Path('/data', name).read_bytes()).hexdigest()
assert all(isinstance(panel.get_password(), str) and isinstance(panel.get_api_key(), str)
           for panel in Panel.objects.all()), 'Existing credential decryption failed'
result['credentials_decrypt'] = True

if '--require-idle' in sys.argv:
    assert not PanelLock.objects.exists(), 'Panel operation locked; defer release'
    # Includes all new health_policy tasks: never downgrade their in-flight verifier.
    pending = PanelTaskTarget.objects.exclude(state__in=(
        'success', 'failed', 'already', 'skipped', 'cancelled', 'completed')).values(
            'state', 'reconcile_requested', 'children', 'task__state', 'task__operation', 'task__audit')
    assert all(failed_renewal_preflight(target) for target in pending), 'Panel task pending; defer release'
    assert not OpsRun.objects.filter(state__in=('queued', 'running')).exists(), 'Operations job active; defer release'
    assert not OpsTarget.objects.filter(state='running').exists(), 'Operations target active; defer release'
    assert not PanelInventoryLease.objects.filter(expires_at__gt=timezone.now()).exclude(token='').exists(), 'Inventory collection active'
    assert not Task.objects.filter(is_active=True).exists(), 'Active schedule; maintenance window required'
    assert not Detection.objects.filter(is_active=True).exists(), 'Active detection; maintenance window required'
    assert not ScheduleHistory.objects.filter(status=0).exists(), 'Scheduled execution in progress'
    assert not DeployRequest.objects.filter(status='2').exists(), 'Deployment in progress'
    assert not DeployRequest.objects.filter(status='1', plan__isnull=False).exists(), 'Planned deployment pending'
    assert not Repository.objects.filter(status='1').exists(), 'Repository build in progress'
    assert not PipeHistory.objects.filter(status=0).exists(), 'Pipeline in progress'
    cutoff = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    assert not ExecHistory.objects.filter(updated_at__gte=cutoff).exists(), 'Recent execution; defer release'
    assert not Transfer.objects.filter(updated_at__gte=cutoff).exists(), 'Recent transfer; defer release'
    redis = get_redis_connection()
    assert not any(redis.llen(key) for key in (settings.EXEC_WORKER_KEY, settings.SCHEDULE_WORKER_KEY,
        settings.MONITOR_WORKER_KEY, settings.SCHEDULE_KEY)), 'Worker queue not empty'
    assert next(redis.scan_iter(match='PID:*', count=100), None) is None, 'Remote execution PID registered'
    database = settings.DATABASES['default']
    db_port = int(database.get('PORT') or 3306)
    db_addresses = {ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(
        database['HOST'], db_port, type=socket.SOCK_STREAM)}
    for filename, family in (('/proc/net/tcp', socket.AF_INET), ('/proc/net/tcp6', socket.AF_INET6)):
        path = Path(filename)
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] not in ('01', '02', '03'):
                continue
            local_port = int(fields[1].split(':')[1], 16)
            raw_address, raw_port = fields[2].split(':')
            packed = bytes.fromhex(raw_address)
            packed = packed[::-1] if family == socket.AF_INET else b''.join(
                packed[index:index + 4][::-1] for index in range(0, 16, 4))
            address = ipaddress.ip_address(packed)
            if address.is_loopback or local_port == 80:
                continue
            assert address in db_addresses and int(raw_port, 16) == db_port, 'Outbound operation active; defer release'
    for process in Path('/proc').iterdir():
        if process.name.isdigit():
            try:
                assert (process / 'comm').read_text().strip() not in ('rsync', 'sshfs'), 'File transfer process active'
            except (FileNotFoundError, ProcessLookupError):
                pass
print(json.dumps(result, sort_keys=True))
