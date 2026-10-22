import json
import re
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
