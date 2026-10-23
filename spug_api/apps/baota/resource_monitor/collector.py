"""Whitelist-only metrics and a killable local worker for network wall deadlines."""
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

from apps.baota.client import PanelAPIError, PanelClient
from .probe import MAX_OUTPUT_BYTES, TOTAL_TIMEOUT, ProbeError, run_probe


MAX_VALUE = 2 ** 63 - 1
MAX_CORES = 1024 ** 2
MESSAGES = {
    'panel_unconfigured': '宝塔 API 尚未配置',
    'host_unverified': '主机尚未验证',
    'auth_failed': 'SSH 认证失败，请检查已保存的凭据',
    'timeout': '资源采集超时',
    'unsupported': '仅支持 Linux 资源采集',
    'invalid_response': '资源数据格式不受支持',
    'output_limit': '资源采集输出超限',
    'collection_failed': '资源采集失败，请检查连接和配置',
}
_NUMBER = re.compile(r'^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')
_SIZE = re.compile(r'^\s*([+]?(?:\d+(?:\.\d*)?|\.\d+))\s*([KMGTPE]?)(?:i?B)?\s*$', re.I)


class CollectorError(Exception):
    def __init__(self, code, message=None):
        # Accept only our codes/messages, never remote exception text.
        self.code = code if code in MESSAGES else 'collection_failed'
        super().__init__(MESSAGES[self.code])


def number(value, maximum=MAX_VALUE, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and (len(value) > 64 or not _NUMBER.fullmatch(value.strip())):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0 or result > maximum:
        return None
    if integer:
        return int(result) if result.is_integer() else None
    return result


def scaled(value, multiplier):
    result = number(value, MAX_VALUE / multiplier)
    return number(result * multiplier) if result is not None else None


def size_bytes(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return number(value)
    if not isinstance(value, str) or len(value) > 64:
        return None
    match = _SIZE.fullmatch(value)
    if not match:
        return None
    power = ' KMGTPE'.index(match[2].upper()) if match[2] else 0
    return scaled(match[1], 1024 ** power)


def percent(value):
    if isinstance(value, str) and value.strip().endswith('%'):
        value = value.strip()[:-1].strip()
    return number(value, 100)


def memory(used, total):
    if used is not None and total is not None and used > total:
        used = None
    return {'used_bytes': used, 'total_bytes': total,
            'percent': used / total * 100 if used is not None and total else None}


def disk(path, used, total, usage):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 256:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return None
    if used is not None and total is not None and used > total:
        used = None
    return {'path': path, 'used_bytes': used, 'total_bytes': total, 'percent': percent(usage)}


def empty_metrics():
    return {'cpu': {'percent': None, 'cores': None},
            'memory': memory(None, None), 'load': [None, None, None],
            'network': {'up_bytes_sec': None, 'down_bytes_sec': None}, 'disks': []}


def normalize_panel(raw):
    if (not isinstance(raw, dict) or raw.get('status') is False
            or not any(key in raw for key in ('cpu', 'mem', 'load', 'up', 'down', 'disk'))):
        raise CollectorError('invalid_response')
    result = empty_metrics()
    cpu = raw.get('cpu')
    if isinstance(cpu, (list, tuple)):
        result['cpu']['percent'] = percent(cpu[0]) if cpu else None
        cores = number(cpu[1], MAX_CORES, integer=True) if len(cpu) > 1 else None
        result['cpu']['cores'] = cores if cores else None
    mem = raw.get('mem')
    if isinstance(mem, dict):
        result['memory'] = memory(scaled(mem.get('memRealUsed'), 1024 ** 2),
                                  scaled(mem.get('memTotal'), 1024 ** 2))
    load = raw.get('load')
    if isinstance(load, dict):
        result['load'] = [number(load.get(key), MAX_CORES) for key in ('one', 'five', 'fifteen')]
    result['network'] = {'up_bytes_sec': scaled(raw.get('up'), 1024),
                         'down_bytes_sec': scaled(raw.get('down'), 1024)}
    disks = raw.get('disk')
    if isinstance(disks, list):
        for item in disks:
            if not isinstance(item, dict):
                continue
            values = item.get('size')
            if not isinstance(values, (list, tuple)):
                continue
            values = [*values[:4], None, None, None, None]
            parsed = disk(item.get('path'), size_bytes(values[1]), size_bytes(values[0]), values[3])
            if parsed is not None:
                result['disks'].append(parsed)
            if len(result['disks']) == 32:
                break
    return result


def parse_probe(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        raise CollectorError('invalid_response')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError:
        raise CollectorError('invalid_response') from None
    if not lines or lines[0] != 'SPUG_RESOURCE_V1' or lines[-1] != 'SPUG_RESOURCE_END':
        raise CollectorError('invalid_response')
    result, samples = empty_metrics(), []
    for line in lines[1:-1]:
        values = line.split('\t')
        if line == 'sample':
            samples.append({})
        elif values[0] in ('cpu', 'network') and samples and len(values) == 4:
            samples[-1][values[0]] = values[1:]
        elif values[0] == 'memory' and len(values) == 3:
            total, available = (scaled(x, 1024) for x in values[1:])
            used = total - available if total is not None and available is not None and available <= total else None
            result['memory'] = memory(used, total)
        elif values[0] == 'load' and len(values) == 4:
            result['load'] = [number(x, MAX_CORES) for x in values[1:]]
        elif values[0] == 'disk' and len(values) == 5 and len(result['disks']) < 32:
            parsed = disk(values[4], scaled(values[2], 1024), scaled(values[1], 1024), values[3])
            if parsed is not None:
                result['disks'].append(parsed)
    if len(samples) != 2:
        raise CollectorError('invalid_response')
    first, last = samples
    if 'cpu' in first and 'cpu' in last:
        previous = [number(x) for x in first['cpu'][:2]]
        current = [number(x) for x in last['cpu'][:2]]
        cores = number(last['cpu'][2], MAX_CORES, integer=True)
        result['cpu']['cores'] = cores if cores else None
        if None not in previous + current:
            total, idle = current[0] - previous[0], current[1] - previous[1]
            if total > 0 and 0 <= idle <= total:
                result['cpu']['percent'] = percent((total - idle) / total * 100)
    if 'network' in first and 'network' in last:
        previous = [number(x) for x in first['network']]
        current = [number(x) for x in last['network']]
        if previous[2] is not None and current[2] is not None:
            elapsed = current[2] - previous[2]
            if 0 < elapsed <= TOTAL_TIMEOUT:
                for i, key in enumerate(('up_bytes_sec', 'down_bytes_sec')):
                    if previous[i] is not None and current[i] is not None:
                        result['network'][key] = number((current[i] - previous[i]) / elapsed)
    return result


def _collect_panel(panel):
    try:
        with PanelClient(panel) as client:
            raw = client.request('/system', 'GetNetWork', timeout=(3, 8))
        return normalize_panel(raw)
    except CollectorError:
        raise
    except PanelAPIError as error:
        raise CollectorError('timeout' if '超时' in str(error) else 'collection_failed') from None
    except Exception:
        raise CollectorError('collection_failed') from None


def _collect_host(host):
    try:
        return parse_probe(run_probe(host))
    except CollectorError:
        raise
    except ProbeError as error:
        raise CollectorError(error.code) from None
    except Exception:
        raise CollectorError('collection_failed') from None


def run_worker(payload):
    """Bound DNS/connect/auth/read together, without detached resolver/watchdog threads.

    Credentials exist only in the pipe and process memory, never argv/environment/files.
    The child owns all sockets and Paramiko threads, so kill + wait also reclaims DNS.
    """
    started = time.monotonic()
    child = None
    finished = False
    try:
        wire = json.dumps(payload, allow_nan=False).encode('utf-8')
        child = subprocess.Popen([sys.executable, '-m', 'apps.baota.resource_monitor.worker'],
                                 cwd=str(Path(__file__).resolve().parents[3]),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        remaining = TOTAL_TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired('resource monitor worker', TOTAL_TIMEOUT)
        output, _ = child.communicate(wire, timeout=remaining)
        finished = True  # communicate also waits/reaps the worker.
        if child.returncode != 0 or len(output) > MAX_OUTPUT_BYTES:
            raise CollectorError('collection_failed')
        result = json.loads(output)
        if not isinstance(result, dict):
            raise CollectorError('invalid_response')
        if 'error' in result:
            raise CollectorError(result['error'])
        metrics = result.get('metrics')
        if not isinstance(metrics, dict) or set(metrics) != set(empty_metrics()):
            raise CollectorError('invalid_response')
        return metrics
    except subprocess.TimeoutExpired:
        raise CollectorError('timeout') from None
    except CollectorError:
        raise
    except Exception:
        raise CollectorError('collection_failed') from None
    finally:
        if child is not None:
            if not finished:
                child.kill()
                child.wait()
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    stream.close()


def collect_panel(panel):
    if not getattr(panel, 'api_base_url', '') or not getattr(panel, 'api_key_ciphertext', ''):
        raise CollectorError('panel_unconfigured')
    try:
        payload = {'kind': 'panel', 'api_base_url': panel.api_base_url, 'api_key': panel.get_api_key(),
                   'tls_ca': getattr(panel, 'tls_ca', ''),
                   'tls_fingerprint': getattr(panel, 'tls_fingerprint', '')}
        return run_worker(payload)
    except CollectorError:
        raise
    except Exception:
        raise CollectorError('collection_failed') from None


def collect_host(host):
    if getattr(host, 'is_verified', False) is not True:
        raise CollectorError('host_unverified')
    try:
        return run_worker({'kind': 'host', 'hostname': host.hostname, 'port': host.port or 22,
                           'username': host.username, 'private_key': host.private_key})
    except CollectorError:
        raise
    except Exception:
        raise CollectorError('collection_failed') from None
