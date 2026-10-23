import json
import re
import ssl
from urllib.parse import urlsplit


def parse_body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError):
        raise ValueError('请求数据格式错误')
    if not isinstance(data, dict):
        raise ValueError('请求数据格式错误')
    return data


def positive_id(value, required=True):
    if value in (None, '') and not required:
        return None
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]{0,17}', str(value)):
        raise ValueError('请选择有效记录')
    return int(value)


def text(data, key, label, max_length, required=True, strip=True):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ValueError(f'{label}格式错误')
    if strip:
        value = value.strip()
    if required and not value:
        raise ValueError(f'请输入{label}')
    if len(value) > max_length:
        raise ValueError(f'{label}不能超过 {max_length} 个字符')
    return value


def panel_url(value):
    if re.search(r'[\x00-\x20\x7f\\]', value):
        raise ValueError('面板地址不能包含空格、控制字符或反斜杠')
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError()
        if parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
        if parsed.fragment:
            raise ValueError()
    except ValueError:
        raise ValueError('请输入有效的 HTTP/HTTPS 面板地址，账户密码请填写在独立字段中')
    return value


def api_base_url(value):
    if not value:
        return ''
    panel_url(value)
    if '?' in value or '#' in value:
        raise ValueError('API 地址不能包含查询参数或片段，请与登录安全入口分开填写')
    return value.rstrip('/')


def api_configuration(data, panel):
    """Only supplied fields change; legacy CRUD clients leave API settings untouched."""
    values = {}
    for field, label, limit in [('api_base_url', 'API 地址', 2048),
                                ('tls_ca', '可信 CA 证书', 65536),
                                ('tls_fingerprint', '证书指纹', 95)]:
        values[field] = (text(data, field, label, limit, required=False, strip=False)
                         if field in data else getattr(panel, field))
    values['api_base_url'] = api_base_url(values['api_base_url'])
    fingerprint = values['tls_fingerprint']
    if fingerprint:
        if not re.fullmatch(r'(?:[0-9a-fA-F]{64}|(?:[0-9a-fA-F]{2}:){31}[0-9a-fA-F]{2})', fingerprint):
            raise ValueError('请输入完整的 SHA-256 证书指纹')
        values['tls_fingerprint'] = fingerprint.replace(':', '').lower()
    if values['tls_ca'] and fingerprint:
        raise ValueError('可信 CA 和固定指纹只能选择一种')
    if values['tls_ca'] or fingerprint:
        if urlsplit(values['api_base_url']).scheme != 'https':
            raise ValueError('证书信任配置仅适用于 HTTPS API 地址')
    ca = values['tls_ca']
    if ca:
        if not re.fullmatch(r'\s*(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?'
                            r'-----END CERTIFICATE-----\s*)+', ca):
            raise ValueError('请导入 PEM 格式的公开 CA 证书，不要填写私钥')
        try:
            ssl.create_default_context().load_verify_locations(cadata=ca)
        except (ssl.SSLError, ValueError):
            raise ValueError('可信 CA 证书格式错误') from None
    clear = data.get('clear_api_key', False)
    if not isinstance(clear, bool):
        raise ValueError('清除 API 密钥参数格式错误')
    key = text(data, 'api_key', 'API 密钥', 4096, required=False, strip=False)
    if clear and key:
        raise ValueError('不能同时填写和清除 API 密钥')
    return values, key, clear


def is_linux_system(info):
    """Require a recognizable Linux platform or distro from GetSystemTotal."""
    system = ' '.join(info[key].lower() for key in ('system', 'os', 'platform')
                      if isinstance(info.get(key), str))
    if re.search(r'\b(?:windows|win32|darwin|macos|mac os|freebsd|openbsd|netbsd|solaris|sunos|aix|hp-ux|plan\s?9)\b', system):
        return False
    return bool(re.search(r'\b(?:linux|ubuntu|debian|centos|rocky|almalinux|red hat|fedora|'
                          r'opensuse|suse|arch|alpine|gentoo|cloudlinux|euleros|openeuler|anolis)\b', system))
