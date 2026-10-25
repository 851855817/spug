"""Administrator-only platform diagnostics; never connects to customer servers."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from django.conf import settings
from .health_probe import NAMES, SERVICES, STARTABLE, STOPPED, row

CACHE_SECONDS = 15
LIMITATIONS = ['本页仅检查 Spug 平台自身，不采集客户服务器。',
               '进程运行不代表业务任务成功；队列仅为等待数。',
               '检查结果最多缓存 15 秒；宿主机报告按独立诊断周期更新。',
               '备份存在或摘要正确不等于已完成恢复演练。']


def now():
    return datetime.now(timezone.utc)


def state_dir():
    path = Path(getattr(settings, 'PLATFORM_HEALTH_DIR', '/data/spug-health'))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def unknown(kind):
    return row(kind, NAMES[kind], 'unknown', '检查未完成或超时', '检查对应服务后重试。')


def run_probe(kind):
    if kind not in NAMES:
        raise ValueError('unsupported probe')
    try:
        result = subprocess.run([sys.executable, '-m', 'apps.setting.health_probe', kind],
                                cwd=str(settings.BASE_DIR), capture_output=True, text=True,
                                timeout=4, shell=False)
        if result.returncode != 0 or len(result.stdout) > 32768:
            return unknown(kind)
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            return unknown(kind)
        return data
    except Exception:
        return unknown(kind)


def check_key():
    item = ('key', '宝塔凭据加密密钥')
    try:
        key = Path(getattr(settings, 'BAOTA_KEY_FILE', '/data/baota.key'))
        value = key.read_bytes().strip()
        Fernet(value)
        expected = Path(str(key) + '.sha256').read_text().strip()
        if len(expected) != 64 or not hmac.compare_digest(hashlib.sha256(value).hexdigest(), expected):
            return row(*item, 'error', '密钥校验不一致', '保留当前文件，核对原始备份；不要生成新密钥。')
        if os.name != 'nt' and key.stat().st_mode & 0o077:
            return row(*item, 'warning', '密钥有效，文件权限过宽', '将密钥文件权限设为 600。')
        return row(*item, 'ok', '密钥与保全指纹一致')
    except Exception:
        return row(*item, 'error', '密钥或校验文件缺失、不可读取或格式错误', '核对持久卷与原始备份；不要生成新密钥。')


def check_disk():
    path = getattr(settings, 'PLATFORM_HEALTH_DATA_DIR', '/data')
    checks = []
    try:
        usage = shutil.disk_usage(path)
        percent = usage.used / usage.total * 100
        status = 'error' if usage.free < 512 * 1024**2 or percent >= 90 else 'warning' if percent >= 80 else 'ok'
        checks.append(row('disk', '持久数据盘空间', status,
                          '已用 %.1f%%，剩余 %.1f GiB' % (percent, usage.free / 1024**3),
                          '空间不足时核查日志、备份及任务产物。' if status != 'ok' else ''))
    except Exception:
        checks.append(row('disk', '持久数据盘空间', 'unknown', '无法读取数据盘空间'))
    try:
        fs = os.statvfs(path)
        if fs.f_files <= 0:
            raise ValueError('unsupported filesystem')
        percent = (1 - fs.f_favail / fs.f_files) * 100
        status = 'error' if percent >= 95 else 'warning' if percent >= 85 else 'ok'
        checks.append(row('inode', '持久数据盘 Inode', status, '已用 %.1f%%' % percent,
                          '核查大量小文件；磁盘有空间也可能耗尽 Inode。' if status != 'ok' else ''))
    except Exception:
        checks.append(row('inode', '持久数据盘 Inode', 'unknown', '此环境无法读取 Inode'))
    return checks


def check_host_report():
    item = ('host', '宿主机独立诊断')
    try:
        path = Path(getattr(settings, 'PLATFORM_HOST_HEALTH_FILE', '/data/platform-health.json'))
        if path.stat().st_size > 131072:
            raise ValueError('oversize')
        report = json.loads(path.read_text(encoding='utf-8'))
        checked = datetime.fromisoformat(report['checked_at'].replace('Z', '+00:00'))
        age = (now() - checked).total_seconds()
        if age < -60 or age > 600:
            return row(*item, 'warning', '宿主机报告已过期或时间异常', '通过 SSH 运行独立诊断，检查采集定时器。')
        status = report['status']
        checks = report['checks']
        # Match the independent host producer's aggregate precedence.
        statuses = ('error', 'warning', 'unknown', 'ok')
        if status not in statuses or not isinstance(checks, list) or not 1 <= len(checks) <= 32:
            raise ValueError('invalid report')
        check_ids, check_statuses = set(), set()
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError('invalid report')
            check_id, check_status = check.get('id'), check.get('status')
            if (not isinstance(check_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', check_id)
                    or check_id in check_ids or check_status not in statuses):
                raise ValueError('invalid report')
            check_ids.add(check_id)
            check_statuses.add(check_status)
        if status != next(value for value in statuses if value in check_statuses):
            raise ValueError('invalid report')
        # Never echo arbitrary strings from a filesystem report.
        return row(*item, status, '独立报告：%s（%d 秒前）' % (
            {'ok': '正常', 'warning': '有提醒', 'error': '异常', 'unknown': '待核实'}[status], max(0, age)),
            '宿主机报告位于 /opt/spug/platform-health.json；备份状态与恢复演练分别核实。')
    except Exception:
        return row(*item, 'unknown', '未取得有效宿主机报告', '通过 SSH 执行独立诊断并检查报告同步。')


def overall(checks):
    statuses = {c['status'] for c in checks}
    return 'error' if 'error' in statuses else 'unknown' if 'unknown' in statuses else 'warning' if 'warning' in statuses else 'ok'


def _snapshot():
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {kind: pool.submit(run_probe, kind) for kind in NAMES}
        checks = []
        for kind, future in futures.items():
            try:
                value = future.result()
                checks.extend(value['checks'] if kind == 'supervisor' and 'checks' in value else [value])
            except Exception:
                checks.append(unknown(kind))
    checks.extend(check_disk())
    checks.extend([check_key(), check_host_report()])
    return dict(checked_at=now().isoformat(), status=overall(checks), checks=checks, limitations=LIMITATIONS)


@contextmanager
def operation_lock(name='recovery'):
    file = open(state_dir() / (name + '.lock'), 'a+b')
    locked = False
    try:
        os.chmod(file.name, 0o600)
        if os.name == 'nt':
            import msvcrt
            if file.tell() == 0:
                file.write(b'0')
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            if os.name == 'nt':
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)
        file.close()


def snapshot():
    try:
        path = state_dir() / 'snapshot.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            age = (now() - datetime.fromisoformat(data['checked_at'])).total_seconds()
            if 0 <= age < CACHE_SECONDS:
                return data
        except Exception:
            pass
        with operation_lock('snapshot'):
            data = _snapshot()
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            temp.chmod(0o600)
            temp.replace(path)
            return data
    except OSError:
        return dict(checked_at=now().isoformat(), status='unknown', checks=[
            row('snapshot', '健康检查', 'unknown', '正在检查或状态目录不可写', '稍后重试；持续失败请检查数据盘权限。')], limitations=LIMITATIONS)


def service_states():
    data = run_probe('supervisor')
    return {r['service']: r['process_state'] for r in data.get('checks', []) if r.get('service') in SERVICES}


def audit(actor_id, service, status):
    path = state_dir() / 'recovery.jsonl'
    with path.open('a', encoding='utf-8') as file:
        path.chmod(0o600)
        file.write(json.dumps(dict(checked_at=now().isoformat(), actor_id=actor_id, service=service, status=status)) + '\n')
        file.flush()
        os.fsync(file.fileno())


def recover_service(service, actor_id):
    refused = dict(status='refused', summary='未执行：服务运行中、状态未知或不允许启动。')
    if not isinstance(service, str) or service not in STARTABLE:
        return refused
    try:
        with operation_lock(), operation_lock('snapshot'):
            if service_states().get(service) not in STOPPED:
                return refused
            # Fail closed before any mutation if its audit trail cannot be written.
            audit(actor_id, service, 'requested')
            result = dict(status='uncertain', summary='启动结果待核实，请重新检查状态；不要重复提交。')
            try:
                command = subprocess.run(['supervisorctl', '-c', '/etc/supervisord.conf', 'start', service],
                                         capture_output=True, text=True, timeout=4, shell=False)
                if command.returncode == 0 and service_states().get(service) == 'RUNNING':
                    result = dict(status='started', summary='服务已启动，请重新检查平台及任务状态。')
            except Exception:
                pass
            try:
                audit(actor_id, service, result['status'])
            except OSError:
                result = dict(status='uncertain', summary='操作已发出，但最终审计写入失败，请通过 SSH 核实。')
            try:
                (state_dir() / 'snapshot.json').unlink(missing_ok=True)
            except OSError:
                pass
            return result
    except Exception:
        return dict(status='refused', summary='未执行：健康检查或恢复操作进行中，或无法写入审计记录。')
