"""Bounded child probes. Only fixed checks and sanitized results leave this process."""
import json
import os
import subprocess
import sys

SERVICES = ('nginx', 'redis', 'spug-api', 'spug-monitor', 'spug-panel-worker',
            'spug-scheduler', 'spug-worker', 'spug-ws', 'spug-ops-worker')
STARTABLE = tuple(name for name in SERVICES if name != 'redis')
STOPPED = ('STOPPED', 'EXITED', 'FATAL')
STATES = ('RUNNING', 'STARTING', 'BACKOFF', 'STOPPING', 'UNKNOWN') + STOPPED
NAMES = {'database': '数据库', 'redis': 'Redis 与任务队列', 'supervisor': '平台进程'}


def row(id, name, status, summary, advice='', **extra):
    return dict(id=id, name=name, status=status, summary=summary, advice=advice, **extra)


def parse_services(text):
    states = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in SERVICES and parts[1] in STATES:
            states[parts[0]] = parts[1]
    checks = []
    for service in SERVICES:
        state = states.get(service, 'UNKNOWN')
        status = ('ok' if state == 'RUNNING' else 'error' if state in STOPPED
                  else 'unknown' if state == 'UNKNOWN' else 'warning')
        checks.append(row('service.' + service, service, status, '进程状态：' + state,
                          '' if state == 'RUNNING' else '检查服务日志及依赖；启动后重新检查。',
                          service=service, process_state=state,
                          recoverable=service in STARTABLE and state in STOPPED))
    return {'checks': checks}


def probe(kind):
    if kind not in NAMES:
        raise ValueError('unsupported probe')
    if kind == 'supervisor':
        result = subprocess.run(['supervisorctl', '-c', '/etc/supervisord.conf', 'status'],
                                capture_output=True, text=True, timeout=2, shell=False)
        return parse_services(result.stdout)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
    import django
    django.setup()
    if kind == 'database':
        from django.db import connection
        if connection.vendor == 'mysql':
            connection.settings_dict.setdefault('OPTIONS', {}).update(
                connect_timeout=2, read_timeout=2, write_timeout=2)
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone()[0] != 1:
                raise ValueError('unexpected response')
        return row(kind, NAMES[kind], 'ok', '数据库查询正常')
    from django.conf import settings
    from django_redis import get_redis_connection
    client = get_redis_connection('default')
    client.connection_pool.connection_kwargs.update(socket_connect_timeout=2, socket_timeout=2)
    if not client.ping():
        raise ValueError('unexpected response')
    keys = (settings.EXEC_WORKER_KEY, settings.SCHEDULE_WORKER_KEY, settings.MONITOR_WORKER_KEY)
    counts = [client.llen(key) for key in keys]
    return row(kind, NAMES[kind], 'warning' if sum(counts) >= 100 else 'ok',
               'Redis 正常；等待队列：执行 %d、计划 %d、监控 %d' % tuple(counts),
               '等待数不包含正在执行的任务。', waiting=dict(zip(('exec', 'schedule', 'monitor'), counts)))


if __name__ == '__main__':
    kind = sys.argv[1] if len(sys.argv) == 2 else ''
    try:
        result = probe(kind)
    except Exception:
        result = row(kind if kind in NAMES else 'probe', NAMES.get(kind, '探针'),
                     'unknown', '检查未完成', '检查服务及依赖后重试；详细原因请在服务器诊断。')
    print(json.dumps(result, ensure_ascii=False))
