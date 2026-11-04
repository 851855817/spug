"""Bounded non-interactive SSH: fixed commands and strictly typed scalar results."""
import json
import errno
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
    def __init__(self, category, uncertain=False, *, reason='', exit_code=None, php_version=None):
        self.category = category if category in MESSAGES else 'check_error'
        self.uncertain = bool(uncertain)
        details = {
            'dns': 'DNS 解析失败，未取得主机 IP；检查主机地址和 DNS。',
            'refused': 'TCP 连接被拒绝；检查 SSH 端口和 sshd 是否监听。',
            'unreachable': 'TCP 网络不可达；检查路由、安全组及防火墙。',
            'tcp_timeout': 'TCP 连接超时（最长 10 秒）；检查端口、安全组和网络。',
            'key': 'SSH 私钥为空、格式无效或需要口令；核对主机管理中的密钥。',
            'auth': 'SSH 密钥认证失败；核对登录用户、authorized_keys 和登录权限。',
            'handshake': 'SSH 握手或连接中断；检查 SSH 服务、端口及网络。',
            'channel': 'SSH 会话通道建立失败；检查 SSH 会话限制。',
            'json': '检查输出不是有效 JSON；核对解释器、启动提示及检查程序。',
            'no_metrics': '检查完成但未返回有效指标；核对依赖版本和系统支持。',
            'output_limit': '检查输出超过 64 KiB，已停止读取；检查异常启动提示或解释器。',
            'python_missing': '远端返回退出码 127，未找到所需命令；核对 python3 是否已安装并在 PATH 中。',
            'php_command': 'PHP 检查返回退出码 127；核对所选 PHP CLI 能否启动及其运行依赖。',
            'handshake_timeout': 'SSH 握手或认证阶段超时；检查 SSH 服务、网络和认证响应。',
            'channel_timeout': 'SSH 会话通道建立超时；检查 SSH 会话限制和主机负载。',
            'execute_timeout': 'SSH 命令发送或读取超时；核对主机负载和网络。',
            'deadline': 'SSH 连接或检查超过总时限，未自动重试；检查主机负载与网络。',
        }
        message = details.get(reason, MESSAGES[self.category])
        if reason == 'php_missing' and php_version in catalogue.PHP_VERSIONS:
            message = '所选 PHP CLI 不存在或不可执行：/www/server/php/%s/bin/php；检查是否安装对应版本。' % php_version
        if type(exit_code) is int and -1 <= exit_code <= 255 and reason != 'python_missing':
            message += '（退出码 %s）' % exit_code
        if self.uncertain:
            message += ' 本台结果待核实，不会自动重发。'
        super().__init__(message[:200])


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
        raise ProbeError('check_error', reason='no_metrics')
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
        raise ProbeError('check_error', reason='no_metrics')
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
        raise ProbeError('connection', reason='dns')
    last_reason = ''
    for family, socktype, proto, _, address in addresses[:8]:
        stream = socket.socket(family, socktype, proto)
        try:
            stream.settimeout(_remaining(connect_deadline))
            stream.connect(address)
            return stream
        except socket.timeout:
            stream.close()
            raise ProbeError('timeout', reason='tcp_timeout') from None
        except OSError as exc:
            stream.close()
            last_reason = ('refused' if exc.errno in (errno.ECONNREFUSED, 10061) else
                           'unreachable' if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065) else '')
    raise ProbeError('connection', reason=last_reason)


def run_probe(host, job, params):
    command = catalogue.command(job, params)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    client, channel, stream = SSHClient(), None, None
    dispatched = False
    phase = 'key'
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
            raise ProbeError('ssh_auth', reason='key')
        try:
            private_key = SSH.load_pkey(key)
        except (ValueError, SSHException):
            raise ProbeError('ssh_auth', reason='key') from None
        phase = 'connect'
        stream = _connect_socket(host.hostname, host.port or 22, deadline)
        phase = 'handshake'
        client.set_missing_host_key_policy(AutoAddPolicy())
        client.connect(hostname=host.hostname, port=host.port or 22, username=host.username,
                       pkey=private_key, sock=stream, timeout=_remaining(deadline),
                       auth_timeout=_remaining(deadline), banner_timeout=_remaining(deadline),
                       allow_agent=False, look_for_keys=False)
        transport = client.get_transport()
        if not transport or not transport.is_active():
            raise ProbeError('connection')
        phase = 'channel'
        channel = transport.open_session(timeout=_remaining(deadline))
        channel.settimeout(_remaining(deadline))
        channel.set_combine_stderr(True)
        dispatched = True
        phase = 'execute'
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
                    raise ProbeError('check_error', reason='output_limit')
            elif channel.exit_status_ready():
                break
            else:
                time.sleep(min(0.05, _remaining(deadline)))
        exit_code = channel.recv_exit_status()
        if exit_code == 42:
            raise ProbeError('dependency', reason='php_missing' if job == 'php_environment' else '',
                             php_version=params.get('php_version', '74'), exit_code=exit_code)
        if exit_code == 127:
            raise ProbeError('dependency', reason='php_command' if job == 'php_environment' else 'python_missing')
        if exit_code:
            raise ProbeError('check_error', exit_code=exit_code)
        try:
            payload = json.loads(bytes(output).decode('utf-8'))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ProbeError('check_error', reason='json') from None
        checks = parse_probe(job, payload)
        _remaining(deadline)
        if expired.is_set():
            raise ProbeError('timeout')
        return checks
    except ProbeError as exc:
        if expired.is_set():
            raise ProbeError('timeout', uncertain=dispatched, reason='deadline') from None
        if dispatched and exc.category in ('timeout', 'connection'):
            raise ProbeError(exc.category, uncertain=True) from None
        raise
    except AuthenticationException:
        raise ProbeError('timeout' if expired.is_set() else 'ssh_auth', uncertain=dispatched,
                         reason='deadline' if expired.is_set() else 'auth') from None
    except (socket.timeout, TimeoutError):
        reason = 'deadline' if expired.is_set() or time.monotonic() >= deadline else {
            'handshake': 'handshake_timeout', 'channel': 'channel_timeout',
            'execute': 'execute_timeout', 'connect': 'tcp_timeout',
        }.get(phase, '')
        raise ProbeError('timeout', uncertain=dispatched, reason=reason) from None
    except (SSHException, OSError, EOFError):
        raise ProbeError('timeout' if expired.is_set() else 'connection', uncertain=dispatched,
                         reason='deadline' if expired.is_set() else 'channel' if phase == 'channel' else 'handshake') from None
    except Exception:
        raise ProbeError('timeout' if expired.is_set() else 'check_error',
                         uncertain=dispatched and expired.is_set()) from None
    finally:
        watchdog.cancel()
        close_resources()
