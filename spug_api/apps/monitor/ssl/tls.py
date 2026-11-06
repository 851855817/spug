"""Public SNI TLS checks with pinned public DNS answers and a process deadline."""
import ipaddress
import re
import socket
import ssl
from libs.worker_authorization import authorize_remote

TLS_MESSAGES = {
    'dns_failed': '公网 DNS 解析失败', 'handshake_failed': '公网 TLS 连接或握手失败',
    'verified': '公网证书链、有效期及域名验证通过', 'expired': '公网证书已过期',
    'not_yet_valid': '公网证书尚未生效', 'hostname_mismatch': '公网证书域名不匹配',
    'self_signed': '公网证书为自签名，未受信任', 'untrusted': '公网证书链未受信任',
    'verification_failed': '公网证书验证失败：证书链、域名或有效期不可信',
}

def normalize_domain(value, allow_wildcard=False):
    if not isinstance(value, str) or not value or len(value) > 253 or any(c.isspace() for c in value):
        raise ValueError('域名格式无效')
    wildcard = value.startswith('*.')
    if wildcard and not allow_wildcard:
        raise ValueError('通配符不能用于公网握手')
    value = value[2:] if wildcard else value
    try:
        value = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ValueError('域名格式无效') from None
    if len(value) > 253 or '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', v)
                                                for v in value.split('.')):
        raise ValueError('域名格式无效')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError('公网检查仅接受 DNS 域名')
    return '*.' + value if wildcard else value


def validate_port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('TLS 端口无效')
    return port


def failure(message):
    return {'verified': False, 'certificate': {}, 'message': message}


def _public_addresses(domain, port):
    # This resolver runs only in a disposable child. Its result is never resolved
    # a second time by connect(), including the unverified metadata fallback.
    authorize_remote()
    rows = socket.getaddrinfo(domain, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not rows or len(rows) > 32:
        raise ValueError('公网 DNS 结果为空或超出限制')
    result = []
    for family, socktype, protocol, _, address in rows:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise ValueError('公网 DNS 结果不受支持')
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or
                getattr(ip, 'ipv4_mapped', None) is not None or '%' in address[0]):
            raise ValueError('公网检查拒绝私有、回环及特殊网络地址')
        endpoint = (family, socktype, protocol, address)
        if endpoint not in result:
            result.append(endpoint)
    return result[:4]


def _handshake(endpoint, domain, context, timeout=5):
    from .collectors import public_certificate
    family, socktype, protocol, address = endpoint
    raw = socket.socket(family, socktype, protocol)
    try:
        raw.settimeout(timeout)
        authorize_remote()
        raw.connect(address)  # Already-resolved numeric sockaddr, never a hostname.
        authorize_remote()
        with context.wrap_socket(raw, server_hostname=domain) as secured:
            der = secured.getpeercert(binary_form=True)
            return public_certificate(ssl.DER_cert_to_PEM_cert(der))
    finally:
        raw.close()


def _check_tls(domain, port, *, peer_hostname=None, max_endpoints=4, timeout=5):
    try:
        domain, port = normalize_domain(domain), validate_port(port)
        peer = peer_hostname or domain
        if peer_hostname is not None:
            if not isinstance(peer_hostname, str) or not peer_hostname or len(peer_hostname) > 253:
                raise ValueError('域名格式无效')
            try:
                ipaddress.ip_address(peer_hostname)
            except ValueError:
                peer = normalize_domain(peer_hostname)
        endpoints = _public_addresses(peer, port)
    except ValueError as error:
        return failure(str(error))
    except Exception:
        return failure(TLS_MESSAGES['dns_failed'])
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    for endpoint in endpoints[:max_endpoints]:
        try:
            certificate = _handshake(endpoint, domain, context, timeout=timeout)
            return {'verified': True, 'certificate': certificate, 'message': TLS_MESSAGES['verified']}
        except ssl.SSLCertVerificationError as error:
            reason = {10: 'expired', 9: 'not_yet_valid', 62: 'hostname_mismatch', 18: 'self_signed',
                      19: 'self_signed', 20: 'untrusted', 21: 'untrusted'}.get(getattr(error, 'verify_code', None),
                                                                             'verification_failed')
            result = failure(TLS_MESSAGES[reason])
            try:
                fallback = ssl._create_unverified_context()
                fallback.minimum_version = ssl.TLSVersion.TLSv1_2
                result['certificate'] = _handshake(endpoint, domain, fallback, timeout=timeout)
            except Exception:
                pass
            return result
        except Exception:
            continue
    return failure(TLS_MESSAGES['handshake_failed'])


def check_tls(domain, port=443):
    from .collectors import CollectorError, run_worker
    try:
        domain, port = normalize_domain(domain), validate_port(port)
        return run_worker({'action': 'tls', 'domain': domain, 'port': port})
    except ValueError as error:
        return failure(str(error))
    except CollectorError as error:
        return failure(str(error))
