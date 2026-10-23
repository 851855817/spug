"""Bounded, signed Baota API transport. Remote response bodies never become errors."""
import hashlib
import http.client
import io
import json
import math
import os
import re
import secrets
import socket
import ssl
import stat
import tempfile
import time
import unicodedata
from contextvars import ContextVar
from urllib.parse import unquote_to_bytes, urlsplit

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import HTTPError as Urllib3HTTPError, ReadTimeoutError

from .security import SecretError
from .validation import api_base_url, api_configuration


DEFAULT_TIMEOUT = (5, 20)
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
FILE_CHUNK_BYTES = 8192
RESERVED_PARAMS = frozenset(('request_time', 'request_token', 'action'))
_request_deadline = ContextVar('baota_request_deadline', default=None)


class PanelAPIError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class _RequestDeadline:
    def __init__(self, timeout):
        self.expires_at = time.monotonic() + sum(timeout)

    def remaining(self):
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise socket.timeout('Panel request total deadline exceeded')
        return remaining


class _DeadlineReader(io.RawIOBase):
    def __init__(self, raw, sock, deadline):
        super().__init__()
        self.raw, self.sock, self.deadline = raw, sock, deadline
        self.idle_timeout = sock.gettimeout()

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline.remaining()
        self.sock.settimeout(min(self.idle_timeout, remaining) if self.idle_timeout else remaining)
        return self.raw.readinto(buffer)

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(sock, *args, **kwargs)
        deadline = _request_deadline.get()
        if deadline is not None:
            # HTTPResponse has just created its socket BufferedReader; no bytes are read yet.
            # Replace its raw SocketIO before begin() so each recv, including header readline
            # and urllib3's internal chunk fills, observes the same monotonic wall deadline.
            # SocketIO also handles SSLSocket, preserving TLS validation and decrypted data.
            raw = self.fp.detach()
            self.fp = io.BufferedReader(_DeadlineReader(raw, sock, deadline))


class _DeadlineConnectionMixin:
    response_class = _DeadlineResponse

    def connect(self):
        deadline = _request_deadline.get()
        if deadline is not None:
            remaining = deadline.remaining()
            self.timeout = min(self.timeout, remaining) if self.timeout else remaining
        super().connect()
        if deadline is not None:
            deadline.remaining()  # Do not send a signature after a slow connect consumed the budget.

    def send(self, data):
        deadline = _request_deadline.get()
        if deadline is not None:
            if self.sock is None and self.auto_open:
                self.connect()
            remaining = deadline.remaining()
            if self.sock is not None:
                # urllib3 sends each body chunk separately. Refresh the remaining budget
                # for headers and every sendall, including pooled keep-alive sockets.
                self.sock.settimeout(min(self.timeout, remaining) if self.timeout else remaining)
        return super().send(data)


class _DeadlineHTTPConnection(_DeadlineConnectionMixin, HTTPConnection):
    pass


class _DeadlineHTTPSConnection(_DeadlineConnectionMixin, HTTPSConnection):
    pass


class _DeadlineHTTPPool(HTTPConnectionPool):
    ConnectionCls = _DeadlineHTTPConnection


class _DeadlineHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _DeadlineHTTPSConnection


class _DeadlineAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)
        # Copy the adapter's mapping: changing urllib3's module-level mapping affects other clients.
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            'http': _DeadlineHTTPPool, 'https': _DeadlineHTTPSPool,
        }


class _FingerprintAdapter(_DeadlineAdapter):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        # urllib3 checks the exact leaf during TLS connect, before any signed HTTP bytes.
        # This dedicated session uses the pin as its trust anchor, including self-signed leaves.
        pool_kwargs.update(assert_fingerprint=self.fingerprint, assert_hostname=False)
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


class _NoRedirectSession(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Requests otherwise prefetches a redirect body to prepare response.next even with
        # allow_redirects=False, bypassing our streaming size limit before we can reject it.
        return iter(())


def _timeout(value):
    value = DEFAULT_TIMEOUT if value is None else value
    pair = value if isinstance(value, (tuple, list)) else (value, value)
    if len(pair) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                             or not math.isfinite(v) or v <= 0 for v in pair) or not math.isfinite(sum(pair)):
        raise PanelAPIError('面板请求超时配置无效')
    return tuple(pair)


def _file_limit(maximum):
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 < maximum <= MAX_FILE_BYTES:
        raise PanelAPIError('文件传输大小限制无效')
    return maximum


def _contains_timeout(error):
    # Requests wraps a blocked send as ConnectionError(ProtocolError(..., TimeoutError)).
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (socket.timeout, requests.exceptions.Timeout, ReadTimeoutError)):
            return True
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))
    return False


def _file_component(value):
    if (not isinstance(value, str) or not value or value in ('.', '..')
            or any(character in '/\\&|' or unicodedata.category(character) in ('Cc', 'Cf', 'Cs')
                   for character in value) or len(value.encode('utf-8')) > 255):
        raise PanelAPIError('文件名格式无效')
    return value


def _file_path(value, *, directory=False):
    # /download html.unescape()s its filename and splits on '|'. Never allow
    # either aliasing mechanism, nor dot components or alternate separators.
    if (not isinstance(value, str) or not value.startswith('/') or len(value) > 4096
            or value == '/' and not directory):
        raise PanelAPIError('文件绝对路径格式无效')
    if value != '/':
        for component in value[1:].split('/'):
            _file_component(component)
    if len(value.encode('utf-8')) > 4096:
        raise PanelAPIError('文件绝对路径格式无效')
    return value


class _MultipartFile:
    """Known-length multipart body; only framing and one small file chunk live in memory."""
    def __init__(self, directory, filename, stream, signature, maximum):
        try:
            if not isinstance(stream, io.BytesIO) and not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError()
            if not stream.seekable():
                raise ValueError()
            position = stream.tell()
            try:
                end = stream.seek(0, os.SEEK_END)
            finally:
                stream.seek(position)
            if not 0 <= position <= end or end - position > maximum:
                raise ValueError()
            if not isinstance(stream.read(0), bytes):
                raise ValueError()
        except (AttributeError, OSError, TypeError, ValueError):
            raise PanelAPIError('上传文件不可读取或超出大小限制') from None
        self.stream, self.remaining = stream, end - position
        self.boundary = secrets.token_hex(24)
        prefix = bytearray()
        for name, value in {'action': 'UploadFile', 'path': directory, **signature}.items():
            prefix.extend(('--' + self.boundary + '\r\nContent-Disposition: form-data; name="'
                           + name + '"\r\n\r\n' + value + '\r\n').encode('utf-8'))
        escaped = filename.replace('"', '\\"')
        prefix.extend(('--' + self.boundary + '\r\nContent-Disposition: form-data; name="zunfile"; '
                       + 'filename="' + escaped + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('utf-8'))
        suffix = ('\r\n--' + self.boundary + '--\r\n').encode('ascii')
        self.prefix, self.suffix = io.BytesIO(prefix), io.BytesIO(suffix)
        self.length = len(prefix) + self.remaining + len(suffix)

    def __len__(self):
        return self.length

    def read(self, size=-1):
        size = min(size, FILE_CHUNK_BYTES) if size >= 0 else FILE_CHUNK_BYTES
        result = bytearray(self.prefix.read(size))
        if len(result) < size and self.remaining:
            count = min(size - len(result), self.remaining)
            chunk = self.stream.read(count)
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > count:
                raise OSError('Upload source changed or is not binary')
            self.remaining -= len(chunk)
            result.extend(chunk)
        if len(result) < size and not self.remaining:
            result.extend(self.suffix.read(size - len(result)))
        return bytes(result)


def _download_disposition(disposition, expected):
    """Accept one attachment basename, including Werkzeug's Unicode fallback + filename*."""
    match = re.match(r'attachment(?=\s*;)', disposition, re.I)
    if not match or re.search(r'[\x00-\x1f\x7f-\x9f]', disposition):
        return False
    rest, values = disposition[match.end():], {}
    parameter = re.compile(r'\s*;\s*(filename\*?)\s*=\s*(?:"((?:[^"\\]|\\["\\])*)"|([^;\s]+))\s*', re.I)
    while rest:
        match = parameter.match(rest)
        if not match or match.group(1).lower() in values:
            return False
        name = match.group(1).lower()
        value = re.sub(r'\\(["\\])', r'\1', match.group(2)) if match.group(2) is not None else match.group(3)
        values[name], rest = value, rest[match.end():]
    if 'filename*' in values:
        encoded = values['filename*']
        if not encoded.lower().startswith("utf-8''") or re.search(r'%(?![a-fA-F0-9]{2})', encoded):
            return False
        try:
            name = unquote_to_bytes(encoded[7:]).decode('utf-8')
        except (UnicodeError, ValueError):
            return False
        fallback = unicodedata.normalize('NFKD', expected).encode('ascii', 'ignore').decode('ascii')
        return name == expected and ('filename' not in values or values['filename'] in (expected, fallback))
    return values.get('filename') == expected


class PanelClient:
    def __init__(self, panel):
        self.session = _NoRedirectSession()
        self.session.trust_env = False  # No implicit proxies, netrc credentials or global TLS overrides.
        self._ca_path = None
        self._key = ''
        self._closed = False
        try:
            configuration, _, _ = api_configuration({}, panel)
            self.base_url = configuration['api_base_url']
            self._key = panel.get_api_key()
            if not self.base_url or not self._key:
                raise PanelAPIError('请先保存 API 地址和 API 密钥')
            self.verify = True
            self.session.mount('http://', _DeadlineAdapter(max_retries=0))
            if configuration['tls_fingerprint']:
                self.session.mount('https://', _FingerprintAdapter(configuration['tls_fingerprint']))
                # Chain/hostname trust is replaced only here by urllib3's verified SHA-256 leaf pin.
                self.verify = False
            else:
                self.session.mount('https://', _DeadlineAdapter(max_retries=0))
                if configuration['tls_ca']:
                    fd, self._ca_path = tempfile.mkstemp(prefix='spug-baota-ca-', suffix='.pem')
                    with os.fdopen(fd, 'w', encoding='ascii') as stream:
                        stream.write(configuration['tls_ca'])
                    self.verify = self._ca_path
        except (SecretError, ValueError, OSError):
            self.close()
            raise PanelAPIError('API 配置或加密密钥不可用，请检查已保存的配置') from None
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.session.close()
        self._key = ''
        self._closed = True
        if self._ca_path:
            try:
                os.unlink(self._ca_path)
            except FileNotFoundError:
                pass
            self._ca_path = None

    def request(self, path, action=None, params=None, mutating=False, timeout=None, *, allow_status_false=False):
        if self._closed:
            raise PanelAPIError('面板连接已关闭')
        if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
                or re.search(r'[\x00-\x20\x7f\\?#]', path)):
            raise PanelAPIError('面板 API 路径无效')
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise PanelAPIError('面板 API 路径必须属于当前面板')
        if params is not None and not isinstance(params, dict):
            raise PanelAPIError('面板 API 参数格式错误')
        params = dict(params or {})
        if RESERVED_PARAMS.intersection(params):
            raise PanelAPIError('不能覆盖面板 API 签名或操作参数')
        if action is not None and (not isinstance(action, str) or not re.fullmatch(r'[A-Za-z0-9_]+', action)):
            raise PanelAPIError('面板 API 操作无效')
        timeout = _timeout(timeout)
        # requests read timeouts only bound inactivity. The sum also bounds total header/body
        # duration, even when a peer continuously trickles bytes. OS DNS lookup remains subject
        # to the system resolver; no detached resolver/watchdog threads are created here.
        deadline = _RequestDeadline(timeout)
        request_time = str(int(time.time()))
        params.update(request_time=request_time, request_token=hashlib.md5(
            (request_time + hashlib.md5(self._key.encode('utf-8')).hexdigest()).encode('ascii')).hexdigest())
        response = None
        deadline_token = _request_deadline.set(deadline)
        try:
            response = self.session.post(self.base_url + path,
                                         params={'action': action} if action is not None else None,
                                         data=params, timeout=timeout, allow_redirects=False,
                                         verify=self.verify, stream=True)
            if 300 <= response.status_code < 400:
                raise PanelAPIError('面板 API 返回重定向，请检查 API 地址', uncertain=mutating)
            if response.status_code != 200:
                raise PanelAPIError('面板 API HTTP 请求失败，请检查地址、白名单和 API 配置', uncertain=mutating)
            content_length = response.headers.get('Content-Length', '')
            if content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
                raise PanelAPIError('面板 API 响应过大', uncertain=mutating)
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PanelAPIError('面板 API 响应过大', uncertain=mutating)
            try:
                result = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                raise PanelAPIError('面板 API 响应格式错误', uncertain=mutating) from None
            if not isinstance(result, (dict, list)):
                raise PanelAPIError('面板 API 响应格式错误', uncertain=mutating)
            deadline.remaining()
            # A few resource APIs (e.g. GetSSL) use status:false for an absent resource.
            # Only internal adapters opt in; callers must still select safe output fields.
            if isinstance(result, dict) and result.get('status') is False and not allow_status_false:
                raise PanelAPIError('面板 API 拒绝请求，请检查 API 密钥、IP 白名单及操作条件', uncertain=mutating)
            return result
        except requests.exceptions.SSLError:
            raise PanelAPIError('面板 HTTPS 证书验证失败，请检查 CA 或固定指纹', uncertain=mutating) from None
        except (requests.exceptions.Timeout, socket.timeout):
            raise PanelAPIError('面板 API 请求超时', uncertain=mutating) from None
        except requests.exceptions.ConnectionError as exc:
            # requests wraps streaming ReadTimeoutError in ConnectionError.
            if any(isinstance(arg, ReadTimeoutError) for arg in exc.args) or time.monotonic() >= deadline.expires_at:
                raise PanelAPIError('面板 API 请求超时', uncertain=mutating) from None
            raise PanelAPIError('无法连接面板 API，请检查地址和网络', uncertain=mutating) from None
        except (requests.exceptions.RequestException, OSError, ValueError):
            if time.monotonic() >= deadline.expires_at:
                raise PanelAPIError('面板 API 请求超时', uncertain=mutating) from None
            raise PanelAPIError('无法连接面板 API，请检查地址和网络', uncertain=mutating) from None
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                _request_deadline.reset(deadline_token)

    def _file_signature(self):
        request_time = str(int(time.time()))
        return {'request_time': request_time, 'request_token': hashlib.md5(
            (request_time + hashlib.md5(self._key.encode('utf-8')).hexdigest()).encode('ascii')).hexdigest()}

    def upload_file(self, directory, filename, stream, *, maximum=MAX_FILE_BYTES, timeout=None):
        """One bounded signed UploadFile request; caller retains ownership of the source stream.

        Reads from its current position without buffering the file or rewinding after sending.
        Any failure after starting the request is uncertain: the panel may have written bytes.
        """
        if self._closed:
            raise PanelAPIError('面板连接已关闭')
        directory, filename = _file_path(directory, directory=True), _file_component(filename)
        timeout, maximum = _timeout(timeout), _file_limit(maximum)
        multipart = _MultipartFile(directory, filename, stream, self._file_signature(), maximum)
        deadline = _RequestDeadline(timeout)
        deadline_token = _request_deadline.set(deadline)
        response = None
        try:
            response = self.session.post(self.base_url + '/files', data=multipart,
                                         headers={'Content-Type': 'multipart/form-data; boundary=' + multipart.boundary},
                                         timeout=timeout, allow_redirects=False, verify=self.verify, stream=True)
            if 300 <= response.status_code < 400:
                raise PanelAPIError('面板上传返回重定向，请检查 API 地址', uncertain=True)
            if response.status_code != 200:
                raise PanelAPIError('面板文件上传 HTTP 请求失败', uncertain=True)
            length = response.headers.get('Content-Length', '')
            if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                raise PanelAPIError('面板上传响应过大', uncertain=True)
            body = bytearray()
            for chunk in response.iter_content(chunk_size=FILE_CHUNK_BYTES):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PanelAPIError('面板上传响应过大', uncertain=True)
            try:
                result = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                raise PanelAPIError('面板上传响应格式错误', uncertain=True) from None
            if not isinstance(result, dict) or result.get('status') is not True:
                raise PanelAPIError('面板未确认文件上传成功', uncertain=True)
            deadline.remaining()
            return result
        except PanelAPIError:
            raise
        except requests.exceptions.SSLError:
            raise PanelAPIError('面板 HTTPS 证书验证失败，请检查 CA 或固定指纹', uncertain=True) from None
        except (requests.exceptions.Timeout, socket.timeout):
            raise PanelAPIError('面板文件上传超时', uncertain=True) from None
        except (requests.exceptions.RequestException, OSError, ValueError) as exc:
            if time.monotonic() >= deadline.expires_at or _contains_timeout(exc):
                raise PanelAPIError('面板文件上传超时', uncertain=True) from None
            raise PanelAPIError('面板文件上传连接失败，请核对 API 和文件状态', uncertain=True) from None
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                _request_deadline.reset(deadline_token)

    def file_sha256(self, filename, maximum, *, timeout=None):
        """Hash one canonical file through signed readonly /download; never return its contents."""
        if self._closed:
            raise PanelAPIError('面板连接已关闭')
        filename = _file_path(filename)
        maximum, timeout = _file_limit(maximum), _timeout(timeout)
        expected = filename.rsplit('/', 1)[1]
        image_types = {'.png': {'image/png'}, '.gif': {'image/gif'},
                       '.jpg': {'image/jpeg', 'image/jpg'}, '.jpeg': {'image/jpeg', 'image/jpg'}}
        allowed_types = {'application/octet-stream'} | image_types.get(os.path.splitext(expected)[1].lower(), set())
        deadline = _RequestDeadline(timeout)
        deadline_token = _request_deadline.set(deadline)
        response = None
        try:
            response = self.session.get(self.base_url + '/download', params={'filename': filename},
                                        data=self._file_signature(), headers={'Accept-Encoding': 'identity'},
                                        timeout=timeout, allow_redirects=False, verify=self.verify, stream=True)
            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if (response.status_code != 200 or content_type not in allowed_types
                    or response.headers.get('Content-Encoding', 'identity').lower() != 'identity'
                    or not _download_disposition(response.headers.get('Content-Disposition', ''), expected)):
                raise PanelAPIError('文件只读下载失败或返回格式无效')
            length = response.headers.get('Content-Length', '')
            if length.isdigit() and int(length) > maximum:
                raise PanelAPIError('文件超出校验大小限制')
            digest, size = hashlib.sha256(), 0
            while True:
                deadline.remaining()
                chunk = response.raw.read(min(FILE_CHUNK_BYTES, maximum - size + 1), decode_content=False)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise PanelAPIError('文件超出校验大小限制')
                digest.update(chunk)
            deadline.remaining()
            return {'sha256': digest.hexdigest(), 'size': size}
        except PanelAPIError:
            raise
        except (requests.exceptions.RequestException, OSError, ValueError, Urllib3HTTPError):
            raise PanelAPIError('文件只读校验失败，请核对 API 和文件状态') from None
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                _request_deadline.reset(deadline_token)

    def read_rewrite_template(self, server, name):
        """Only the fixed template directory, via the reviewed readonly /download route.

        GetFileBody can CREATE missing paths containing 'rewrite'. Never use it,
        even after an existence check (deletion between calls would still write).
        Both reviewed versions authenticate /download through comm.local/get_sk;
        get_input merges GET form body with query, so signatures need not enter URLs.
        Server access logs may contain the non-secret fixed filename.
        """
        if (self._closed or server not in ('nginx', 'apache') or not isinstance(name, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}', name)):
            raise PanelAPIError('伪静态模板标识无效')
        filename = '/www/server/panel/rewrite/' + server + '/' + name + '.conf'
        return self._read_fixed_public_file(filename, name + '.conf', 65536)

    def read_issued_certificate(self, domain):
        """Read only the selected ACME order's public fullchain; never a key file."""
        if (self._closed or not isinstance(domain, str) or len(domain) > 253 or '.' not in domain
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in domain.split('.')) or domain.split('.')[-1].isdigit()):
            raise PanelAPIError('公开证书域名无效')
        filename = '/www/server/panel/vhost/letsencrypt/' + domain + '/fullchain.pem'
        return self._read_fixed_public_file(filename, 'fullchain.pem', 128 * 1024)

    @staticmethod
    def _ssl_site_name(name):
        # Internal server-derived site selector, never an arbitrary filesystem path.
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', name)
                or '..' in name or name.endswith('.')):
            raise PanelAPIError('网站 SSL 配置标识无效')
        return name

    def read_site_web_config(self, server, site_name):
        """Read the selected PHP site's fixed main config; missing files stay missing.

        /download does not construct panelSite or use GetFileBody (which can create
        missing rewrite files). No project-prefix fallback, glob or include expansion.
        """
        if self._closed or server not in ('nginx', 'apache'):
            raise PanelAPIError('网站 SSL 配置服务器类型无效')
        name = self._ssl_site_name(site_name)
        filename = '/www/server/panel/vhost/' + server + '/' + name + '.conf'
        return self._read_fixed_public_file(filename, name + '.conf', 256 * 1024)

    def read_site_public_certificate(self, site_name):
        """Only canonical public fullchain; never resolve caller/config-provided paths."""
        if self._closed:
            raise PanelAPIError('面板连接已关闭')
        name = self._ssl_site_name(site_name)
        filename = '/www/server/panel/vhost/cert/' + name + '/fullchain.pem'
        return self._read_fixed_public_file(filename, 'fullchain.pem', 128 * 1024)

    def _read_fixed_public_file(self, filename, expected, maximum):
        # Internal only: callers above construct the entire path from fixed scopes.
        request_time = str(int(time.time()))
        signature = {'request_time': request_time, 'request_token': hashlib.md5(
            (request_time + hashlib.md5(self._key.encode('utf-8')).hexdigest()).encode('ascii')).hexdigest()}
        timeout = _timeout(None)
        deadline = _RequestDeadline(timeout)
        deadline_token = _request_deadline.set(deadline)
        response = None
        try:
            response = self.session.get(self.base_url + '/download', params={'filename': filename},
                                        data=signature, timeout=timeout, allow_redirects=False,
                                        verify=self.verify, stream=True)
            disposition = response.headers.get('Content-Disposition', '')
            if (response.status_code != 200
                    or response.headers.get('Content-Type', '').split(';', 1)[0].lower() != 'application/octet-stream'
                    or not re.fullmatch(r'attachment;\s*filename=(?:' + re.escape(expected)
                                        + r'|"' + re.escape(expected) + r'")', disposition, re.I)):
                raise PanelAPIError('公开文件只读下载失败或返回格式无效')
            length = response.headers.get('Content-Length', '')
            if length.isdigit() and int(length) > maximum:
                raise PanelAPIError('公开文件超出读取限制')
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > maximum:
                    raise PanelAPIError('公开文件超出读取限制')
            deadline.remaining()
            text = body.decode('utf-8')
            if '\x00' in text:
                raise PanelAPIError('公开文件内容格式无效')
            return text
        except PanelAPIError:
            raise
        except (requests.exceptions.RequestException, OSError, ValueError):
            raise PanelAPIError('公开文件只读下载失败，请核对 API 和文件状态') from None
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                _request_deadline.reset(deadline_token)


def inspect_certificate(base_url):
    """Inspect the public leaf without authenticating and without saving any trust decision."""
    try:
        parsed = urlsplit(api_base_url(base_url))
        if parsed.scheme != 'https':
            raise PanelAPIError('证书检查需要已保存的 HTTPS API 地址')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=DEFAULT_TIMEOUT[0]) as sock:
            sock.settimeout(DEFAULT_TIMEOUT[0])
            with context.wrap_socket(sock, server_hostname=parsed.hostname) as tls:
                certificate = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
        expires = getattr(certificate, 'not_valid_after_utc', None)
        if expires is None:
            import datetime
            expires = certificate.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        return {'fingerprint': certificate.fingerprint(hashes.SHA256()).hex(),
                'subject': certificate.subject.rfc4514_string()[:1000],
                'expires_at': expires.isoformat()}
    except PanelAPIError:
        raise
    except (OSError, ValueError):
        raise PanelAPIError('无法读取面板 HTTPS 证书，请检查已保存的地址和网络') from None
