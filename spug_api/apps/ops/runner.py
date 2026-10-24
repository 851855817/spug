"""Bounded non-interactive SSH: fixed commands and strictly typed scalar results."""
import json
import math
import queue
import re
import socket
import threading
import time

from paramiko import AuthenticationException, AutoAddPolicy, SSHClient, SSHException
from libs.ssh import SSH
from . import catalogue


TOTAL_TIMEOUT = 60
MAX_OUTPUT = 64 * 1024
MESSAGES = {
    'ssh_auth': 'SSH 认证失败，请检查主机凭据',
    'connection': 'SSH 连接失败，请检查地址与网络',
    'timeout': 'SSH 连接或检查超时，未自动重试',
    'dependency': '缺少该标准作业所需依赖',
    'check_error': '检查结果无效或采集异常，未保存原始输出',
    'interrupted': 'Worker 中断，发送或采集结果待核实，不会自动重发',
    'host_changed': '主机身份或凭据已变化，请重新创建作业',
    'permission_denied': '创建者权限已变化，未下发检查',
}


class ProbeError(Exception):
    def __init__(self, category, uncertain=False):
        self.category = category if category in MESSAGES else 'check_error'
        self.uncertain = bool(uncertain)
        super().__init__(MESSAGES[self.category])


def _scalar(value, kind):
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'integer':
        return type(value) is int and 1 <= value <= 65536
    if kind in ('number', 'percent'):
        maximum = 100 if kind == 'percent' else 1_000_000
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum
    if not isinstance(value, str):
        return False
    pattern = r'[a-z][a-z0-9_-]{0,39}' if kind == 'identifier' else r'[0-9][A-Za-z0-9.+_-]{0,79}'
    return bool(re.fullmatch(pattern, value))


def parse_probe(job, payload):
    schema = catalogue.SCHEMAS.get(job)
    if not schema or not isinstance(payload, dict) or not isinstance(payload.get('values'), dict):
        raise ProbeError('check_error')
    checks = []
    for key, label, kind, rule in schema:
        value = payload['values'].get(key)
        if not _scalar(value, kind):
            checks.append({'key': key, 'label': label, 'status': 'unknown', 'value': None,
                           'summary': '未采集到有效数据'})
            continue
        warning = ((rule == 'enabled' and value is True) or (rule == 'disabled' and value is False)
                   or (rule == 'high' and value >= 90) or (rule == 'low' and value < 10))
        checks.append({'key': key, 'label': label, 'status': 'warning' if warning else 'ok',
                       'value': value, 'summary': '需要关注' if warning else '已采集'})
    if not any(x['status'] != 'unknown' for x in checks):
        raise ProbeError('check_error')
    return checks


def _remaining(deadline, cap=10):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProbeError('timeout')
    return min(cap, remaining)


def _connect_socket(hostname, port, deadline):
    """DNS has no socket timeout; only the lookup lives in a bounded-wait daemon.

    A timed-out lookup can finish later but cannot connect or send a command.
    Connections use an already-resolved socket, keeping Paramiko away from DNS.
    """
    result = queue.Queue(maxsize=1)
    def lookup():
        try:
            result.put(socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
    threading.Thread(target=lookup, daemon=True).start()
    connect_deadline = min(deadline, time.monotonic() + 10)
    try:
        addresses = result.get(timeout=_remaining(connect_deadline))
    except queue.Empty:
        raise ProbeError('timeout') from None
    if not addresses:
        raise ProbeError('connection')
    for family, socktype, proto, _, address in addresses[:8]:
        stream = socket.socket(family, socktype, proto)
        try:
            stream.settimeout(_remaining(connect_deadline))
            stream.connect(address)
            return stream
        except socket.timeout:
            stream.close()
            raise ProbeError('timeout') from None
        except OSError:
            stream.close()
    raise ProbeError('connection')


def run_probe(host, job, params):
    command = catalogue.command(job, params)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    client, channel, stream = SSHClient(), None, None
    dispatched = False
    expired = threading.Event()

    def close_resources():
        # Closing the socket and transport also releases Paramiko's unbounded
        # exec-request acknowledgment and exit-status event waits.
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        for resource in (channel, client, stream):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass

    def timeout():
        expired.set()
        close_resources()

    watchdog = threading.Timer(TOTAL_TIMEOUT, timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        key = host.private_key
        if not isinstance(key, str) or not key or len(key.encode()) > 32768:
            raise ProbeError('ssh_auth')
        try:
            private_key = SSH.load_pkey(key)
        except (ValueError, SSHException):
            raise ProbeError('ssh_auth') from None
        stream = _connect_socket(host.hostname, host.port or 22, deadline)
        client.set_missing_host_key_policy(AutoAddPolicy())
        client.connect(hostname=host.hostname, port=host.port or 22, username=host.username,
                       pkey=private_key, sock=stream, timeout=_remaining(deadline),
                       auth_timeout=_remaining(deadline), banner_timeout=_remaining(deadline),
                       allow_agent=False, look_for_keys=False)
        transport = client.get_transport()
        if not transport or not transport.is_active():
            raise ProbeError('connection')
        channel = transport.open_session(timeout=_remaining(deadline))
        channel.settimeout(_remaining(deadline))
        channel.set_combine_stderr(True)
        dispatched = True
        channel.exec_command(command)
        output = bytearray()
        while True:
            _remaining(deadline)
            if channel.recv_ready():
                data = channel.recv(min(4096, MAX_OUTPUT + 1 - len(output)))
                if not data:
                    if channel.exit_status_ready():
                        break
                    time.sleep(min(0.05, _remaining(deadline)))
                    continue
                output.extend(data)
                if len(output) > MAX_OUTPUT:
                    raise ProbeError('check_error')
            elif channel.exit_status_ready():
                break
            else:
                time.sleep(min(0.05, _remaining(deadline)))
        exit_code = channel.recv_exit_status()
        if exit_code in (42, 127):
            raise ProbeError('dependency')
        if exit_code:
            raise ProbeError('check_error')
        try:
            payload = json.loads(bytes(output).decode('utf-8'))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ProbeError('check_error') from None
        checks = parse_probe(job, payload)
        _remaining(deadline)
        if expired.is_set():
            raise ProbeError('timeout')
        return checks
    except ProbeError as exc:
        if expired.is_set():
            raise ProbeError('timeout', uncertain=dispatched) from None
        if dispatched and exc.category in ('timeout', 'connection'):
            raise ProbeError(exc.category, uncertain=True) from None
        raise
    except AuthenticationException:
        raise ProbeError('timeout' if expired.is_set() else 'ssh_auth', uncertain=dispatched) from None
    except (socket.timeout, TimeoutError):
        raise ProbeError('timeout', uncertain=dispatched) from None
    except (SSHException, OSError, EOFError):
        raise ProbeError('timeout' if expired.is_set() else 'connection', uncertain=dispatched) from None
    except Exception:
        raise ProbeError('timeout' if expired.is_set() else 'check_error',
                         uncertain=dispatched and expired.is_set()) from None
    finally:
        watchdog.cancel()
        close_resources()
