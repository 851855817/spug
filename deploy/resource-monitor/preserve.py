"""Private before/after fingerprints; prints no customer fields or credentials."""
import hashlib
import json
import os
from pathlib import Path
import sys
import socket
import ipaddress
from datetime import datetime, timedelta

sys.path.insert(0, '/data/spug/spug_api')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
import django
django.setup()
from django.apps import apps
from apps.baota.models import Panel, PanelLock, PanelTaskTarget
from apps.host.models import Host, Group
from apps.exec.models import ExecTemplate, ExecHistory, Transfer
from apps.setting.models import Setting
from apps.schedule.models import Task, History as ScheduleHistory
from apps.deploy.models import DeployRequest
from apps.repository.models import Repository
from apps.pipeline.models import PipeHistory
from apps.monitor.models import Detection
from django.conf import settings
from django_redis import get_redis_connection


def digest(value):
    body = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()
    return hashlib.sha256(body).hexdigest()


result = {}
models = [model for model in apps.get_app_config('baota').get_models()
          if not model.__name__.startswith('ResourceMonitor')]
models += [Host, Group, Group.hosts.through, ExecTemplate, ExecHistory, Transfer, Setting,
           Task, ScheduleHistory, DeployRequest, Repository, PipeHistory, Detection]
for model in models:
    rows = list(model.objects.order_by('pk').values())
    result[model._meta.db_table] = {'count': len(rows), 'digest': digest(rows)}
for name in ('baota.key', 'baota.key.sha256'):
    result[name] = hashlib.sha256(Path('/data', name).read_bytes()).hexdigest()
assert all(isinstance(panel.get_password(), str) and isinstance(panel.get_api_key(), str)
           for panel in Panel.objects.all()), 'Existing credential decryption failed'
result['credentials_decrypt'] = True
if '--require-idle' in sys.argv:
    assert not PanelLock.objects.exists(), 'Pending customer lock; do not deploy'
    assert not PanelTaskTarget.objects.exclude(state__in=(
        'success', 'failed', 'already', 'skipped', 'cancelled', 'completed')).exists(), 'Pending task; do not deploy'
    # This bounded demo release does not stop or disable user-configured producers.
    # Refuse an active schedule/detection instead of racing its next dispatch.
    assert not Task.objects.filter(is_active=True).exists(), 'Active schedule; wait for a maintenance window'
    assert not Detection.objects.filter(is_active=True).exists(), 'Active detection; wait for a maintenance window'
    assert not ScheduleHistory.objects.filter(status=0).exists(), 'Scheduled execution in progress'
    assert not DeployRequest.objects.filter(status='2').exists(), 'Deployment in progress'
    assert not DeployRequest.objects.filter(status='1', plan__isnull=False).exists(), 'Planned deployment pending'
    assert not Repository.objects.filter(status='1').exists(), 'Repository build in progress'
    assert not PipeHistory.objects.filter(status=0).exists(), 'Pipeline in progress'
    cutoff = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    assert not ExecHistory.objects.filter(updated_at__gte=cutoff).exists(), 'Recent execution; defer release'
    assert not Transfer.objects.filter(updated_at__gte=cutoff).exists(), 'Recent transfer; defer release'
    rds = get_redis_connection()
    assert not any(rds.llen(key) for key in (settings.EXEC_WORKER_KEY, settings.SCHEDULE_WORKER_KEY,
                                           settings.MONITOR_WORKER_KEY, settings.SCHEDULE_KEY)), 'Worker queue not empty'
    assert next(rds.scan_iter(match='PID:*', count=100), None) is None, 'Remote execution PID still registered'
    # Some legacy executions (Windows or long-lived commands) have no PID key.
    # Conservatively refuse any active nonlocal egress except the app database.
    db = settings.DATABASES['default']
    db_addresses = {ipaddress.ip_address(item[4][0]) for item in
                    socket.getaddrinfo(db['HOST'], int(db.get('PORT') or 3306), type=socket.SOCK_STREAM)}
    for filename, family in (('/proc/net/tcp', socket.AF_INET), ('/proc/net/tcp6', socket.AF_INET6)):
        if not Path(filename).exists():
            continue
        for line in Path(filename).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] not in ('01', '02', '03'):  # ESTABLISHED / SYN_SENT / SYN_RECV
                continue
            local_port = int(fields[1].split(':')[1], 16)
            raw_address, raw_port = fields[2].split(':')
            packed = bytes.fromhex(raw_address)
            packed = packed[::-1] if family == socket.AF_INET else b''.join(
                packed[i:i+4][::-1] for i in range(0, 16, 4))
            address = ipaddress.ip_address(packed)
            if address.is_loopback or local_port == 80:
                continue
            assert address in db_addresses and int(raw_port, 16) == int(db.get('PORT') or 3306), \
                'Active outbound connection; defer release until execution/transfer is idle'
    # Legacy transfer jobs do not register PID keys; their rsync/sshfs children do.
    for proc in Path('/proc').iterdir():
        if proc.name.isdigit():
            try:
                assert (proc/'comm').read_text().strip() not in ('rsync', 'sshfs'), 'File transfer process running'
            except (FileNotFoundError, ProcessLookupError):
                pass
print(json.dumps(result, sort_keys=True))
