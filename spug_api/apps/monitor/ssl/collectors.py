"""Credential-isolated, bounded source collection; only public facts leave here."""
import json
from libs.worker_authorization import current_authorizer, exchange, authorize_remote
from datetime import datetime
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

from apps.baota.client import PanelClient
from apps.baota import resources as panel_resources
from apps.baota.operations.base import digest
from apps.baota.operations.certificates import (certificate_info, main_ssl_configuration,
                                                _apache_directives, _nginx_directives)
from .remote_probe import DISCOVERY_MESSAGES, MAX_ITEMS, MAX_OUTPUT_BYTES
from .tls import normalize_domain, validate_port


MESSAGES = {
    **DISCOVERY_MESSAGES,
    'timeout': 'SSL 检查超时', 'auth_failed': 'SSH 认证失败，请检查已保存凭据',
    'host_unverified': '主机尚未验证', 'panel_unconfigured': '宝塔 API 尚未配置',
    'unsupported': '配置格式、引用范围或运行环境不受支持；未返回不完整快照',
    'invalid_response': 'SSL 检查数据格式无效', 'output_limit': 'SSL 检查输出超限',
    'collection_failed': 'SSL 检查失败，请检查连接和配置',
    'stale_plan': '网站、证书或续签配置已变化，请重新预检',
    'unsupported_renewal': '现有续签配置无法安全执行，请参照官方配置指导',
    'renewal_uncertain': '续签结果待核实；不会自动重试，请执行只读核验',
    'python_missing': '远端缺少可用的 Python 3，无法运行固定只读 SSL 探针',
}
FINGERPRINT = re.compile(r'[0-9a-f]{64}\Z')
PAGE_SIZE = 100


class CollectorError(Exception):
    def __init__(self, code, message=None):
        self.code = code if code in MESSAGES else 'collection_failed'
        super().__init__(MESSAGES[self.code])


def public_certificate(pem):
    try:
        result = certificate_info(pem, require_valid=False)
        names = sorted({normalize_domain(v, allow_wildcard=True) for v in result['domains']})
        return {'fingerprint': result['fingerprint'], 'domains': names,
                'not_before': result['not_before'], 'expires_at': result['expires_at']}
    except Exception:
        raise CollectorError('invalid_response') from None


def _safe_text(value, maximum=512):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CollectorError('invalid_response')
    return value


def _normalize_host(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list) or len(raw['items']) > MAX_ITEMS:
        raise CollectorError('invalid_response')
    items = []
    warnings = [_safe_text(v) for v in raw.get('warnings', [])[:40]]
    for row in raw['items']:
        certificate = public_certificate(row['public_pem']) if row.get('public_pem') else {}
        renewal = row.get('renewal', {})
        if not isinstance(renewal, dict) or type(renewal.get('supported')) is not bool:
            raise CollectorError('invalid_response')
        renewal = {key: renewal.get(key, {} if key in ('summary', 'plan') else '')
                   for key in ('engine', 'supported', 'reason', 'reason_code', 'summary', 'plan')}
        if renewal['supported']:
            # The public certificate is independently parsed locally. The remote
            # order may not claim a different SAN set or an undeployed cert.
            plan = renewal['plan']
            if (not certificate or not isinstance(plan, dict) or
                    (plan.get('domains') and set(plan['domains']) != set(certificate['domains']))):
                renewal = {'engine': renewal['engine'], 'supported': False, 'reason': '订单域名与部署证书不匹配',
                           'reason_code': 'domain_mismatch', 'summary': {}, 'plan': {}}
            else:
                # Public SAN/date evidence is derived only by the shared local
                # parser and frozen alongside the remote configuration identity.
                plan['public_baseline'] = certificate.copy()
        items.append({'domain': normalize_domain(row['domain'], allow_wildcard=True),
                      'port': validate_port(row['port']), 'site_id': _safe_text(row['site_id']),
                      'site_name': _safe_text(row['site_name']), 'cert_path': _safe_text(row['cert_path']),
                      'deployment_verified': bool(certificate),
                      'deployment_message': '已核实网站配置中的公开证书' if certificate else '网站公开证书尚未配置或无法读取',
                      'certificate': certificate, 'renewal': renewal})
    return {'items': items, 'warnings': warnings}


def _panel_table(client, name):
    items, seen = [], set()
    for page in range(1, MAX_ITEMS // PAGE_SIZE + 2):
        raw = client.request('/data', 'getData', {'table': name, 'p': page, 'limit': PAGE_SIZE}, timeout=(3, 7))
        rows = raw.get('data') if isinstance(raw, dict) else None
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise CollectorError('invalid_response')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0 or row['id'] in seen:
                raise CollectorError('invalid_response')
            seen.add(row['id'])
            items.append(row)
            if len(items) > MAX_ITEMS:
                raise CollectorError('output_limit')
        if len(rows) < PAGE_SIZE:
            return items
    raise CollectorError('output_limit')


def _discover_panel(panel):
    items, warnings = [], []
    try:
        with PanelClient(panel) as client:
            sites = _panel_table(client, 'sites')
            domains = _panel_table(client, 'domain')
            server = panel_resources.webserver(client)
            for site in sites:
                name = _safe_text(site.get('name'), 253)
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', name):
                    raise CollectorError('invalid_response')
                raw = client.request('/site', 'GetSSL', {'siteName': name}, timeout=(3, 7), allow_status_false=True)
                if not isinstance(raw, dict):
                    raise CollectorError('invalid_response')
                pem = raw.get('csr') or raw.get('cert', '')
                try:
                    certificate = public_certificate(pem) if pem else {}
                except CollectorError:
                    certificate = {}
                cert_path, ports, supported = '', [443], False
                configuration, configuration_verified = {}, False
                configuration_state = 'configuration_unreadable'
                try:
                    configuration = main_ssl_configuration(client, site, server)
                    text = client.read_site_web_config(server, name)
                    if not isinstance(text, str) or digest(text) != configuration['digest']:
                        raise CollectorError('invalid_response')
                    if type(raw.get('status')) is not bool:
                        configuration_state = 'status_unknown'
                    elif raw['status'] != configuration['enabled']:
                        configuration_state = 'status_conflict'
                    elif not configuration['enabled']:
                        # This proves the panel switch and fixed main file only;
                        # standard includes are recognized, never traversed.
                        configuration_state = 'disabled'
                        certificate = {}
                    else:
                        configuration_state = 'certificate_unreadable'
                        public = public_certificate(client.read_site_public_certificate(name))
                        canonical = certificate
                        if public != canonical:
                            raise CollectorError('invalid_response')
                        configuration_state = 'configuration_unreadable'
                        text = client.read_site_web_config(server, name)
                        if not isinstance(text, str) or digest(text) != configuration['digest']:
                            raise CollectorError('invalid_response')
                        directives = _nginx_directives(text) if server == 'nginx' else _apache_directives(text)
                        ports = set()
                        for context, words in directives:
                            if server == 'nginx' and words[0] == 'listen' and 'ssl' in words[1:]:
                                match = re.fullmatch(r'(?:\[[0-9a-fA-F:]+\]:|[0-9.]+:)?([0-9]{1,5})', words[1])
                                if not match:
                                    raise CollectorError('unsupported')
                                ports.add(validate_port(int(match[1])))
                            elif server == 'apache' and words[0] == 'sslengine':
                                for _, arguments in context:
                                    if not isinstance(arguments, int):
                                        raise CollectorError('unsupported')
                                # The existing parser records VirtualHost IDs,
                                # not bindings. Only the standard 443 adapter is
                                # verified here; custom Apache ports stay unknown.
                                ports.add(443)
                        if not ports:
                            raise CollectorError('unsupported')
                        certificate, cert_path = public, configuration['certificate_path']
                        configuration_verified = configuration['included_configuration_verified']
                        supported = configuration_verified
                        configuration_state = 'verified' if supported else 'deployment_unverified'
                except Exception:
                    warnings.append('宝塔网站部署位置或配置引用未能独立核实；未把 GetSSL 副本当作已部署证书')
                    ports = [443]
                names = []
                for value in domains:
                    if str(value.get('pid')) == str(site['id']):
                        try:
                            names.append(normalize_domain(value.get('name'), allow_wildcard=True))
                        except ValueError:
                            warnings.append('忽略无法握手的面板网站域名')
                if not names:
                    try:
                        names = [normalize_domain(name, allow_wildcard=True)]
                    except ValueError:
                        warnings.append('网站没有可识别的 DNS 域名')
                for domain in sorted(set(names)):
                    for port in sorted(ports):
                        if len(items) >= MAX_ITEMS:
                            raise CollectorError('output_limit')
                        observed = supported
                        row_configuration_state = configuration_state
                        evidence = '已核实网站配置中的公开证书' if observed else ''
                        if certificate and configuration_state == 'deployment_unverified' and cert_path:
                            # Default Baota includes cannot be enumerated through
                            # a strictly read-only API. Observe the managed source
                            # endpoint instead, without claiming a complete path
                            # audit or granting permission to renew unknown config.
                            from .tls import _check_tls
                            peer = urlsplit(getattr(panel, 'api_base_url', '')).hostname
                            if peer and not domain.startswith('*.'):
                                origin = _check_tls(domain, port, peer_hostname=peer, max_endpoints=1, timeout=2)
                                observed = bool(origin.get('certificate', {}).get('fingerprint') == certificate['fingerprint'])
                                if observed:
                                    evidence = '源站 TLS 证书与面板副本一致；未遍历引用'
                                    row_configuration_state = 'verified'
                            if not observed:
                                warnings.append('宝塔网站公开证书副本未与源站 SNI 握手证书匹配，部署尚未核实')
                        plan = {'engine': 'baota', 'site_id': site['id'], 'site_name': name,
                                'main_configuration_digest': configuration.get('digest', ''),
                                'configuration_state': row_configuration_state,
                                'includes_verified': configuration_verified,
                                'public_fingerprint': certificate.get('fingerprint', '')}
                        items.append({'domain': domain, 'port': port, 'site_id': str(site['id']), 'site_name': name,
                                  'cert_path': cert_path, 'certificate': certificate,
                                  'deployment_verified': observed,
                                  'deployment_message': evidence if observed else
                                    ('仅获取宝塔公开证书副本，当前网站部署引用尚未核实' if certificate else
                                     '网站公开证书尚未配置或无法读取'),
                                  'renewal': {'engine': 'baota', 'supported': supported,
                                              'reason': '' if certificate else '网站尚未配置可识别证书',
                                              'reason_code': '' if supported else ('unverified_includes' if observed else 'unsupported_config'),
                                              'summary': {'domain': domain, 'engine': 'baota',
                                                          'impacts': ['宝塔可能同步面板内其他符合条件的同域名证书并重载 Web 服务']},
                                              'plan': plan}})
        return {'items': items, 'warnings': sorted(set(warnings))}
    except CollectorError:
        raise
    except PermissionError:
        raise
    except Exception:
        raise CollectorError('collection_failed') from None


def _read_output(channel, expires):
    output, count = bytearray(), 0
    checked_at = None
    while True:
        now = time.monotonic()
        if now >= expires:
            raise CollectorError('timeout')
        if checked_at is None or now - checked_at >= 1:
            authorize_remote()
            checked_at = now
        progressed = False
        if channel.recv_ready():
            chunk = channel.recv(4096)
            count += len(chunk)
            output.extend(chunk)
            progressed = True
        if channel.recv_stderr_ready():
            count += len(channel.recv_stderr(4096))
            progressed = True
        if count > MAX_OUTPUT_BYTES:
            raise CollectorError('output_limit')
        if not progressed and channel.exit_status_ready():
            status = channel.recv_exit_status()
            if status == 127:
                raise CollectorError('python_missing')
            if status != 0:
                raise CollectorError('unsupported')
            try:
                raw = json.loads(bytes(output))
                if not isinstance(raw, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise CollectorError('invalid_response') from None
            if raw.get('error'):
                raise CollectorError(raw['error'])
            if not isinstance(raw.get('result'), dict):
                raise CollectorError('invalid_response')
            return raw['result']
        if not progressed:
            time.sleep(.02)


def _host_transport(host, action, plan=None):
    from paramiko.ssh_exception import AuthenticationException
    ssh = channel = None
    expires = time.monotonic() + (200 if action == 'renew' else 35)
    try:
        ssh = host.get_ssh()
        ssh.authorize = authorize_remote
        remaining = max(.1, expires - time.monotonic())
        ssh.arguments.update(timeout=remaining, banner_timeout=remaining, auth_timeout=remaining,
                             allow_agent=False, look_for_keys=False)
        authorize_remote()
        client = ssh.get_client()
        authorize_remote()
        channel = client.get_transport().open_session(timeout=max(.1, expires - time.monotonic()))
        channel.settimeout(max(.1, expires - time.monotonic()))
        # Static source is shell-quoted once. Remote values are only bounded JSON
        # on stdin; no uploads, shell variables, eval or source are used.
        source = Path(__file__).with_name('remote_probe.py').read_text(encoding='utf8')
        authorize_remote()
        channel.exec_command('python3 -c ' + shlex.quote(source))
        payload = json.dumps({'action': action, 'plan': plan}, separators=(',', ':'), allow_nan=False).encode('utf8')
        if len(payload) > MAX_OUTPUT_BYTES:
            raise CollectorError('output_limit')
        authorize_remote()
        channel.sendall(payload)
        channel.shutdown_write()
        return _read_output(channel, expires)
    except AuthenticationException:
        raise CollectorError('auth_failed') from None
    except (socket.timeout, TimeoutError):
        raise CollectorError('renewal_uncertain' if action == 'renew' else 'timeout') from None
    except CollectorError:
        raise
    except PermissionError:
        raise
    except Exception:
        raise CollectorError('renewal_uncertain' if action == 'renew' else 'collection_failed') from None
    finally:
        if channel is not None:
            channel.close()
        if ssh is not None and ssh.client is not None:
            ssh.client.close()


def _host_result(raw, plan=None):
    if not isinstance(raw, dict) or not isinstance(raw.get('certificates'), list) or len(raw['certificates']) > MAX_ITEMS:
        raise CollectorError('invalid_response')
    certificates = [{'cert_path': _safe_text(item['cert_path']),
                     'certificate': public_certificate(item['public_pem']) if item.get('public_pem') else {}}
                    for item in raw['certificates']]
    state = raw.get('state', 'observed')
    if state not in ('success', 'unchanged', 'observed', 'uncertain'):
        raise CollectorError('invalid_response')
    if state == 'success':
        before = (plan or {}).get('public_baseline', {})
        try:
            if not before or not certificates:
                raise ValueError()
            for item in certificates:
                after = item['certificate']
                if (item['cert_path'] != plan['cert_path'] or not after or
                        after['fingerprint'] == before['fingerprint'] or set(after['domains']) != set(before['domains']) or
                        datetime.fromisoformat(after['expires_at']) <= datetime.fromisoformat(before['expires_at'])):
                    raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise CollectorError('renewal_uncertain') from None
    return {'state': state, 'message': _safe_text(raw.get('message', '已读取部署公开证书')),
            'changed': raw.get('changed') is True, 'certificates': certificates}


def run_worker(payload):
    """Bound DNS/auth/SSH/TLS in a killable child; secrets only cross stdin."""
    timeout = 220 if payload.get('action') == 'renew' else (15 if payload.get('action') == 'tls' else 40)
    child, finished = None, False
    try:
        wire = json.dumps(payload, allow_nan=False).encode('utf8')
        if len(wire) > MAX_OUTPUT_BYTES:
            raise CollectorError('output_limit')
        child = subprocess.Popen([sys.executable, '-m', 'apps.monitor.ssl.probe_worker'],
                                 cwd=str(Path(__file__).resolve().parents[3]),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        authorize = current_authorizer()
        if authorize:
            output = exchange(child, payload, authorize, timeout, MAX_OUTPUT_BYTES)
        else:
            output, _ = child.communicate(wire, timeout=timeout)
        finished = True
        if child.returncode != 0 or len(output) > MAX_OUTPUT_BYTES:
            raise CollectorError('collection_failed')
        result = json.loads(output)
        if not isinstance(result, dict):
            raise CollectorError('invalid_response')
        if result.get('error'):
            raise CollectorError(result['error'])
        if not isinstance(result.get('result'), dict):
            raise CollectorError('invalid_response')
        return result['result']
    except PermissionError:
        raise
    except subprocess.TimeoutExpired:
        raise CollectorError('renewal_uncertain' if payload.get('action') == 'renew' else 'timeout') from None
    except CollectorError:
        raise
    except PermissionError:
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


def _host_payload(host, action, plan=None):
    if getattr(host, 'is_verified', False) is not True:
        raise CollectorError('host_unverified')
    try:
        return {'kind': 'host', 'action': action, 'hostname': host.hostname, 'port': host.port or 22,
                'username': host.username, 'private_key': host.private_key, 'plan': plan}
    except Exception:
        raise CollectorError('collection_failed') from None


def discover(kind, source):
    if kind == 'host':
        return run_worker(_host_payload(source, 'discover'))
    if kind == 'panel':
        if not getattr(source, 'api_base_url', '') or not getattr(source, 'api_key_ciphertext', ''):
            raise CollectorError('panel_unconfigured')
        try:
            return run_worker({'kind': 'panel', 'action': 'discover', 'api_base_url': source.api_base_url,
                               'api_key': source.get_api_key(), 'tls_ca': source.tls_ca,
                               'tls_fingerprint': source.tls_fingerprint})
        except CollectorError:
            raise
        except Exception:
            raise CollectorError('collection_failed') from None
    raise CollectorError('unsupported')


def renew_host(host, plan):
    return run_worker(_host_payload(host, 'renew', plan))


def verify_host(host, plan):
    return run_worker(_host_payload(host, 'verify', plan))
