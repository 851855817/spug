"""Static, stdlib-only Python 3 probe sent over an existing managed SSH source.

Nothing discovered in configuration is evaluated. Discovery never invokes a web
server or ACME client. Private key paths are compared as strings, never opened.
"""
import base64
import glob
import hashlib
import ipaddress
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time


MAX_FILES = 200
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_CERT_BYTES = 128 * 1024
MAX_ITEMS = 100
MAX_DEPTH = 12
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
PROBE_TIMEOUT = 28
RENEW_TIMEOUT = 180
CONFIG_ROOTS = ('/etc/nginx', '/etc/apache2', '/etc/httpd', '/www/server/nginx/conf',
                '/www/server/apache/conf', '/www/server/panel/vhost/nginx', '/www/server/panel/vhost/apache',
                '/www/server/panel/vhost/rewrite', '/usr/share/nginx/modules-available')
CONFIG_FILES = ('/etc/letsencrypt/options-ssl-nginx.conf', '/etc/letsencrypt/options-ssl-apache.conf')
NGINX_MAIN = ('/etc/nginx/nginx.conf', '/www/server/nginx/conf/nginx.conf')
APACHE_MAIN = ('/etc/apache2/apache2.conf', '/etc/httpd/conf/httpd.conf', '/www/server/apache/conf/httpd.conf')
TOOL_PATHS = {'certbot': ('/usr/bin/certbot', '/usr/local/bin/certbot', '/snap/bin/certbot'),
              'acme.sh': ('/root/.acme.sh/acme.sh',)}
SERVICES = {
    '/etc/nginx/nginx.conf': ('nginx', '/usr/sbin/nginx', '/etc/nginx/nginx.conf'),
    '/www/server/nginx/conf/nginx.conf': ('nginx', '/www/server/nginx/sbin/nginx', '/www/server/nginx/conf/nginx.conf'),
    '/etc/apache2/apache2.conf': ('apache', '/usr/sbin/apache2', '/etc/apache2/apache2.conf'),
    '/etc/httpd/conf/httpd.conf': ('apache', '/usr/sbin/httpd', '/etc/httpd/conf/httpd.conf'),
    '/www/server/apache/conf/httpd.conf': ('apache', '/www/server/apache/bin/httpd', '/www/server/apache/conf/httpd.conf'),
}
SERVICE_PIDS = {
    '/etc/nginx/nginx.conf': ('/run/nginx.pid', '/var/run/nginx.pid'),
    '/www/server/nginx/conf/nginx.conf': ('/www/server/nginx/logs/nginx.pid',),
    '/etc/apache2/apache2.conf': ('/run/apache2/apache2.pid', '/var/run/apache2/apache2.pid'),
    '/etc/httpd/conf/httpd.conf': ('/run/httpd/httpd.pid', '/var/run/httpd/httpd.pid'),
    '/www/server/apache/conf/httpd.conf': ('/www/server/apache/logs/httpd.pid',),
}
SAFE_PATH = re.compile(r'/[A-Za-z0-9_./+@-]{1,510}\Z')
SAFE_ORDER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}\Z')
REASONS = {
    'missing_tool': '未找到唯一且受支持的既有 ACME 客户端，请参照官方指导配置后重新检测',
    'missing_order': '没有找到与当前部署公开证书匹配的既有续签订单',
    'ambiguous_order': '多个既有订单指向该证书，无法唯一确定续签影响',
    'unsupported_hook': '现有订单或全局配置包含自定义 hook/命令，已阻止自动执行',
    'unsupported_config': '现有订单包含动态或尚未适配的配置、验证插件或账户设置',
    'unverified_service': '无法唯一确认 Web 服务及固定配置检查、重载方式',
    'deployment_mismatch': '订单证书或私钥部署路径与当前网站配置不一致',
    'domain_mismatch': '订单域名与当前部署公开证书不一致',
}
CERTBOT_DNS = {
    'dns-cloudflare': ('dns_cloudflare', ('dns_cloudflare_credentials', 'dns_cloudflare_propagation_seconds')),
    'dns-digitalocean': ('dns_digitalocean', ('dns_digitalocean_credentials', 'dns_digitalocean_propagation_seconds')),
    'dns-linode': ('dns_linode', ('dns_linode_credentials', 'dns_linode_propagation_seconds', 'dns_linode_version')),
    'dns-ovh': ('dns_ovh', ('dns_ovh_credentials', 'dns_ovh_propagation_seconds')),
    'dns-rfc2136': ('dns_rfc2136', ('dns_rfc2136_credentials', 'dns_rfc2136_propagation_seconds')),
}
ACME_DNS = {
    'dns_cf': ('CF_Token', 'CF_Account_ID', 'CF_Zone_ID', 'CF_Key', 'CF_Email'),
    'dns_ali': ('Ali_Key', 'Ali_Secret'), 'dns_dp': ('DP_Id', 'DP_Key'),
    'dns_gd': ('GD_Key', 'GD_Secret'), 'dns_ovh': ('OVH_AK', 'OVH_AS', 'OVH_CK', 'OVH_END_POINT'),
}


class ProbeError(Exception):
    pass


def digest(data):
    if isinstance(data, str):
        data = data.encode('utf8')
    return hashlib.sha256(data).hexdigest()


def literal_path(value):
    if not isinstance(value, str) or not SAFE_PATH.fullmatch(value) or '..' in value.split('/'):
        raise ProbeError('dynamic_configuration')
    if os.path.normpath(value).replace('\\', '/') != value:
        raise ProbeError('dynamic_configuration')
    return value


def domain(value):
    if not isinstance(value, str) or not value or len(value) > 253 or any(c.isspace() for c in value):
        raise ValueError('domain')
    wildcard = value.startswith('*.')
    value = value[2:] if wildcard else value
    value = value.rstrip('.').encode('idna').decode('ascii').lower()
    if len(value) > 253 or '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', v)
                                                for v in value.split('.')):
        raise ValueError('domain')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError('domain')
    return '*.' + value if wildcard else value


class Reader:
    def __init__(self, root='/'):
        self.root = os.path.realpath(root)
        self.files = {}
        self.total = 0
        self.started = time.monotonic()

    def check(self):
        if time.monotonic() - self.started > PROBE_TIMEOUT:
            raise ProbeError('timeout')

    def local(self, path):
        literal_path(path)
        result = os.path.join(self.root, path.lstrip('/'))
        # Test roots are also strict; symlinks cannot escape the test filesystem.
        if self.root != os.path.abspath(os.sep) and os.path.commonpath((self.root, os.path.realpath(result))) != self.root:
            raise ProbeError('include_outside_roots')
        return result

    def logical(self, path):
        return '/' + os.path.relpath(path, self.root).replace('\\', '/')

    def exists(self, path):
        return os.path.isfile(self.local(path))

    def link(self, path):
        return os.readlink(self.local(path))

    def read(self, path, public=False, identity=False):
        self.check()
        local = self.local(path)
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
        try:
            fd = os.open(local, flags)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ProbeError('non_regular_file')
                if public:
                    # Stop after the first public block. A key placed before a cert
                    # is rejected without searching/reading farther through it.
                    first = stream.readline(80)
                    if first.strip() != b'-----BEGIN CERTIFICATE-----':
                        return ''
                    value = bytearray(first)
                    while True:
                        line = stream.readline(min(4096, MAX_CERT_BYTES + 1 - len(value)))
                        value.extend(line)
                        if len(value) > MAX_CERT_BYTES or not line:
                            raise ProbeError('certificate_invalid')
                        if line.strip() == b'-----END CERTIFICATE-----':
                            return bytes(value).decode('ascii').replace('\r\n', '\n')
                if info.st_size > (4 * 1024 * 1024 if identity else MAX_FILE_BYTES):
                    raise ProbeError('file_limit')
                content = stream.read((4 * 1024 * 1024 if identity else MAX_FILE_BYTES) + 1)
                if len(content) > (4 * 1024 * 1024 if identity else MAX_FILE_BYTES):
                    raise ProbeError('file_limit')
        except (OSError, UnicodeError):
            raise ProbeError('file_unreadable') from None
        if identity:
            return {'sha256': digest(content), 'realpath': self.logical(os.path.realpath(local)),
                    'size': info.st_size, 'mode': stat.S_IMODE(info.st_mode)}
        if path not in self.files:
            if len(self.files) >= MAX_FILES:
                raise ProbeError('file_limit')
            self.total += len(content)
            if self.total > MAX_TOTAL_BYTES:
                raise ProbeError('total_limit')
            self.files[path] = digest(content)
        try:
            return content.decode('utf8')
        except UnicodeError:
            raise ProbeError('configuration_encoding') from None

    def matches(self, pattern, config=False):
        self.check()
        if not isinstance(pattern, str) or len(pattern) > 512 or '**' in pattern or any(c in pattern for c in '$`\\\n\r\x00{}'):
            raise ProbeError('dynamic_configuration')
        # Glob segments are only allowed in the final filename, never directory
        # recursion. [..] is a filename glob, not a shell expression.
        directory, filename = os.path.split(pattern)
        literal_path(directory)
        if not filename or '/' in filename or '..' in directory.split('/'):
            raise ProbeError('dynamic_configuration')
        if config and pattern not in CONFIG_FILES and not any(directory == root or directory.startswith(root + '/') for root in CONFIG_ROOTS):
            raise ProbeError('include_outside_roots')
        result = []
        # iglob prevents materializing an unbounded directory result.
        for path in glob.iglob(os.path.join(self.local(directory), filename)):
            self.check()
            if len(result) >= MAX_FILES:
                raise ProbeError('file_limit')
            logical = self.logical(path)
            resolved = self.logical(os.path.realpath(path))
            if config and resolved not in CONFIG_FILES and not any(resolved == root or resolved.startswith(root + '/') for root in CONFIG_ROOTS):
                raise ProbeError('include_outside_roots')
            if os.path.isfile(path):
                result.append(logical)
        return sorted(result)


def nginx_tokens(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=';{}')
    lexer.whitespace_split = True
    lexer.commenters = '#'
    # Preserve escapes: a target-controlled escaped path is never normalized into
    # a literal file path. This conservative grammar reports unsupported syntax.
    lexer.escape = ''
    try:
        tokens = list(lexer)
    except ValueError:
        raise ProbeError('configuration_syntax') from None
    result = []
    for token in tokens:
        result.extend(list(token) if token and all(c in ';{}' for c in token) else [token])
    return result


def nginx_document(reader, path, stack=(), depth=0, prefix=None):
    if depth > MAX_DEPTH:
        raise ProbeError('include_depth_limit')
    resolved = os.path.realpath(reader.local(path))
    if resolved in stack:
        raise ProbeError('include_cycle')
    tokens = nginx_tokens(reader.read(path))
    prefix = prefix or os.path.dirname(path).replace('\\', '/')
    result, words = [], []
    for token in tokens:
        if token == ';':
            if words and words[0].lower() == 'include':
                if len(words) != 2:
                    raise ProbeError('dynamic_configuration')
                include = words[1]
                if not include.startswith('/'):
                    include = prefix + '/' + include
                matches = reader.matches(include, config=True)
                if not matches and not any(c in include for c in '*?['):
                    raise ProbeError('include_missing')
                for child in matches:
                    result.extend(nginx_document(reader, child, (*stack, resolved), depth + 1, prefix))
            else:
                result.extend(words + [';'])
            words = []
        elif token in '{}':
            result.extend(words + [token])
            words = []
        else:
            words.append(token)
    if words:
        raise ProbeError('configuration_syntax')
    return result


def nginx_sites(reader, path):
    tokens = nginx_document(reader, path)
    contexts, words, sites, pids = [], [], [], []
    reload_safe = True
    for token in tokens:
        if token == '{':
            if not words:
                raise ProbeError('configuration_syntax')
            if words[0].startswith('ssl_certificate'):
                raise ProbeError('dynamic_configuration')
            if any(part in words[0] for part in ('lua', 'perl', 'javascript', 'exec')):
                reload_safe = False
            site = {'domains': [], 'ports': [], 'certs': [], 'keys': [], 'main': path, 'server': 'nginx'} if words == ['server'] else None
            if words[0] in ('server', 'location') and '$' in ' '.join(words):
                raise ProbeError('dynamic_configuration')
            contexts.append((words[0], site))
            words = []
        elif token == '}':
            if words or not contexts:
                raise ProbeError('configuration_syntax')
            _, site = contexts.pop()
            if site is not None:
                sites.append(site)
        elif token == ';':
            if not words:
                raise ProbeError('configuration_syntax')
            site = next((item for _, item in reversed(contexts) if item is not None), None)
            direct = bool(contexts and contexts[-1][1] is not None)
            name, values = words[0], words[1:]
            if name == 'ssl_conf_command':
                raise ProbeError('dynamic_configuration')
            if any(part in name for part in ('lua', 'perl', 'javascript', 'exec')) or name in ('js_import', 'load_module'):
                reload_safe = False
            if name == 'pid':
                if contexts or len(values) != 1:
                    raise ProbeError('dynamic_configuration')
                pids.append(literal_path(values[0]))
            if site is not None and direct:
                if name == 'server_name':
                    site['domains'].extend(values)
                elif name == 'listen' and 'ssl' in values:
                    if not values:
                        raise ProbeError('configuration_syntax')
                    match = re.fullmatch(r'(?:\[[0-9a-fA-F:]+\]:|[0-9.]+:)?([0-9]{1,5})', values[0])
                    if not match or not 1 <= int(match[1]) <= 65535:
                        raise ProbeError('dynamic_configuration')
                    site['ports'].append(int(match[1]))
                elif name == 'ssl_certificate':
                    if len(values) != 1:
                        raise ProbeError('dynamic_configuration')
                    site['certs'].append(literal_path(values[0]))
                elif name == 'ssl_certificate_key':
                    if len(values) != 1:
                        raise ProbeError('dynamic_configuration')
                    site['keys'].append(literal_path(values[0]))
                elif name.startswith('ssl_certificate'):
                    raise ProbeError('dynamic_configuration')
            elif name in ('ssl_certificate', 'ssl_certificate_by_lua_block', 'ssl_certificate_by_lua_file'):
                raise ProbeError('dynamic_configuration')
            words = []
        else:
            words.append(token)
    if contexts or words:
        raise ProbeError('configuration_syntax')
    for site in sites:
        site['pid_paths'], site['reload_safe'] = pids, reload_safe
    return sites


def apache_document(reader, path, stack=(), depth=0, prefix=None):
    if depth > MAX_DEPTH:
        raise ProbeError('include_depth_limit')
    resolved = os.path.realpath(reader.local(path))
    if resolved in stack:
        raise ProbeError('include_cycle')
    result = []
    prefix = prefix or os.path.dirname(path).replace('\\', '/')
    for line in reader.read(path).splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.rstrip().endswith('\\'):
            raise ProbeError('dynamic_configuration')
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            raise ProbeError('configuration_syntax') from None
        if not words:
            continue
        if '${' in line:
            raise ProbeError('dynamic_configuration')
        if words[0].lower() == 'serverroot':
            if len(words) != 2:
                raise ProbeError('dynamic_configuration')
            prefix = literal_path(words[1])
        if words[0].lower() in ('include', 'includeoptional'):
            if len(words) != 2 or '\\' in line:
                raise ProbeError('dynamic_configuration')
            include = words[1]
            if not include.startswith('/'):
                include = prefix + '/' + include
            matches = reader.matches(include, config=True)
            if not matches and words[0].lower() != 'includeoptional':
                raise ProbeError('include_missing')
            for child in matches:
                result.extend(apache_document(reader, child, (*stack, resolved), depth + 1, prefix))
        else:
            result.append(line)
    return result


def apache_sites(reader, path):
    sites, stack, current, pids = [], [], None, []
    reload_safe = True
    for line in apache_document(reader, path):
        value = line.strip()
        if value.startswith('<'):
            if not value.endswith('>'):
                raise ProbeError('configuration_syntax')
            words = shlex.split(value[1:-1])
            name = words[0].lower()
            if name.startswith('/'):
                if not stack or stack[-1] != name[1:]:
                    raise ProbeError('configuration_syntax')
                stack.pop()
                if name == '/virtualhost':
                    if current['ssl']:
                        sites.append(current)
                    current = None
            else:
                stack.append(name)
                if name == 'virtualhost':
                    if current:
                        raise ProbeError('configuration_syntax')
                    ports = []
                    for binding in words[1:]:
                        match = re.fullmatch(r'(?:\*|\[[0-9a-fA-F:]+\]|[0-9.]+):([0-9]{1,5})', binding)
                        if not match or not 1 <= int(match[1]) <= 65535:
                            raise ProbeError('dynamic_configuration')
                        ports.append(int(match[1]))
                    current = {'domains': [], 'ports': ports, 'certs': [], 'keys': [], 'ssl': False, 'main': path, 'server': 'apache'}
            continue
        words = shlex.split(value, comments=True)
        name, values = words[0].lower(), words[1:]
        if name == 'sslopensslconfcmd':
            raise ProbeError('dynamic_configuration')
        if name == 'pidfile':
            if len(values) != 1:
                raise ProbeError('dynamic_configuration')
            pids.append(literal_path(values[0]))
        if 'lua' in name or 'perl' in name or 'exec' in name or name == 'loadmodule':
            reload_safe = False
        if current and stack[-1] == 'virtualhost':
            if name in ('servername', 'serveralias'):
                if '\\' in value:
                    raise ProbeError('dynamic_configuration')
                current['domains'].extend(values)
            elif name == 'sslengine':
                if len(values) != 1 or values[0].lower() not in ('on', 'off'):
                    raise ProbeError('dynamic_configuration')
                current['ssl'] = values[0].lower() == 'on'
            elif name == 'sslcertificatefile':
                if len(values) != 1 or '\\' in value:
                    raise ProbeError('dynamic_configuration')
                current['certs'].append(literal_path(values[0]))
            elif name == 'sslcertificatekeyfile':
                if len(values) != 1 or '\\' in value:
                    raise ProbeError('dynamic_configuration')
                current['keys'].append(literal_path(values[0]))
        elif name in ('sslcertificatefile', 'servername', 'define'):
            raise ProbeError('dynamic_configuration')
    if stack:
        raise ProbeError('configuration_syntax')
    for site in sites:
        site['pid_paths'], site['reload_safe'] = pids, reload_safe
    return sites


def assignments(text):
    """Parse literal single/double quoted assignments; never shell-unquote/eval."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)=(?:\'([^\']*)\'|"([^"$`\\]*)"|([A-Za-z0-9_./:@,+-]*))', line)
        if not match or match[1] in result:
            raise ProbeError('unsupported_renewal')
        value = next((v for v in match.groups()[1:] if v is not None), '')
        if any(c in value for c in '\r\n\x00'):
            raise ProbeError('unsupported_renewal')
        result[match[1]] = value
    return result


def ini_values(text):
    result, section = {}, ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('['):
            if line not in ('[renewalparams]', '[[webroot_map]]'):
                raise ProbeError('unsupported_renewal')
            section = line
            continue
        if '=' not in line:
            raise ProbeError('unsupported_renewal')
        key, value = [x.strip() for x in line.split('=', 1)]
        full_key = section + key
        if full_key in result or any(c in value for c in '$`\\\x00\r\n'):
            raise ProbeError('unsupported_renewal')
        result[full_key] = value
    return result


def unsupported(code='missing_order', engine=''):
    code = code if code in REASONS else 'unsupported_config'
    return {'engine': engine, 'supported': False, 'reason_code': code, 'reason': REASONS[code], 'summary': {}, 'plan': {}}


def executable_identity(reader, path):
    # Tool contents are hashed locally, not executed. Symlink target and mode are
    # frozen so replacing a wrapper or symlink invalidates a queued plan.
    return reader.read(path, identity=True)


def file_identity(reader, path):
    """Freeze credential path identity without opening or reading secret content."""
    local = reader.local(path)
    info = os.stat(local)
    if not stat.S_ISREG(info.st_mode):
        raise ProbeError('unsupported_renewal')
    return {'realpath': reader.logical(os.path.realpath(local)), 'size': info.st_size,
            'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns, 'inode': info.st_ino}


def certbot_plugin(reader, suffix):
    # Distro-installed plugins have a fixed layout. Other Python environments
    # remain unsupported, rather than importing/executing arbitrary discovery.
    roots = ('/usr/lib/python3/dist-packages', '/usr/lib/python3/site-packages')
    matches = []
    for root in roots:
        path = root + '/certbot_' + suffix + '/_internal/' + suffix + '.py'
        if reader.exists(path):
            matches.append(path)
    if len(matches) != 1:
        raise ProbeError('unsupported_renewal')
    return {'path': matches[0], 'identity': executable_identity(reader, matches[0])}


def runtime_service(reader, service, configured_pids):
    kind, tool, main = service
    allowed = SERVICE_PIDS.get(main, ())
    if len(configured_pids) > 1 or any(path not in allowed for path in configured_pids):
        raise ProbeError('unsupported_renewal')
    paths = configured_pids or [path for path in allowed if reader.exists(path)]
    if not paths:
        raise ProbeError('unsupported_renewal')
    observations = []
    for pid_path in paths:
        value = reader.read(pid_path).strip()
        if not re.fullmatch(r'[1-9][0-9]{0,8}', value):
            raise ProbeError('unsupported_renewal')
        binary = reader.link('/proc/' + value + '/exe')
        if os.path.realpath(reader.local(binary)) != os.path.realpath(reader.local(tool)):
            raise ProbeError('unsupported_renewal')
        cmdline = reader.read('/proc/' + value + '/cmdline')
        if kind == 'nginx':
            if not cmdline.startswith('nginx: master process '):
                raise ProbeError('unsupported_renewal')
            command = cmdline[len('nginx: master process '):].replace('\x00', ' ').strip()
        else:
            command = cmdline.replace('\x00', ' ').strip()
        args = shlex.split(command)
        if not args or args[0] != tool:
            raise ProbeError('unsupported_renewal')
        config_flag = '-c' if kind == 'nginx' else '-f'
        if config_flag in args:
            index = args.index(config_flag)
            if index + 1 >= len(args) or args[index + 1] != main:
                raise ProbeError('unsupported_renewal')
        elif main not in ('/etc/nginx/nginx.conf', '/etc/apache2/apache2.conf', '/etc/httpd/conf/httpd.conf'):
            raise ProbeError('unsupported_renewal')
        # Unknown prefix/global options can change configuration/pid resolution.
        if '-p' in args or '-d' in args or (kind == 'nginx' and '-g' in args and
                not command.endswith('-g daemon on; master_process on;')):
            raise ProbeError('unsupported_renewal')
        stat_data = reader.read('/proc/' + value + '/stat').rsplit(') ', 1)[1].split()
        observations.append({'pid_path': pid_path, 'pid': int(value), 'start_ticks': stat_data[19],
                             'cmdline_hash': digest(cmdline), 'exe': binary})
    if len({entry['pid'] for entry in observations}) != 1:
        raise ProbeError('unsupported_renewal')
    return observations[0]


def known_saved_reload(reader, value, service):
    """Recognize a literal existing acme reload; never execute/normalize shell."""
    if not value:
        return {}
    if len(value) > 2048:
        raise ProbeError('unsupported_renewal')
    prefix, suffix = '__ACME_BASE64__START_', '__ACME_BASE64__END_'
    if value.startswith(prefix) and value.endswith(suffix):
        try:
            value = base64.b64decode(value[len(prefix):-len(suffix)], validate=True).decode('ascii')
        except (ValueError, UnicodeError):
            raise ProbeError('unsupported_renewal') from None
    kind, tool, main = service
    if kind != 'nginx':
        raise ProbeError('unsupported_renewal')
    aliases = [tool]
    allowed = set()
    for alias in aliases:
        allowed.add(alias + ' -t -c ' + main + ' && ' + alias + ' -s reload -c ' + main)
        if main == '/etc/nginx/nginx.conf':
            allowed.add(alias + ' -t && ' + alias + ' -s reload')
    if value not in allowed:
        raise ProbeError('unsupported_renewal')
    return {'command_hash': digest(value), 'policy': 'existing_nginx_test_and_reload'}


def order_plans(reader, sites):
    candidates, failures = [], []
    hooks = []
    for global_config in ('/etc/letsencrypt/cli.ini', '/root/.config/letsencrypt/cli.ini'):
        if reader.exists(global_config) and reader.read(global_config).strip():
            hooks.append('global_config')
    for stage in ('pre', 'deploy', 'post'):
        hooks.extend(reader.matches('/etc/letsencrypt/renewal-hooks/' + stage + '/*'))
    certbot_paths = reader.matches('/etc/letsencrypt/renewal/*.conf')
    acme_paths = []
    # Fixed one-level order folders only. Never recurse /home or a filesystem.
    acme_home = reader.local('/root/.acme.sh')
    if os.path.isdir(acme_home):
        with os.scandir(acme_home) as entries:
            count = 0
            for entry in entries:
                count += 1
                if count > MAX_FILES:
                    raise ProbeError('file_limit')
                if entry.is_dir(follow_symlinks=False) and SAFE_ORDER.fullmatch(entry.name):
                    order = entry.name[:-4] if entry.name.endswith('_ecc') else entry.name
                    candidate = '/root/.acme.sh/' + entry.name + '/' + order + '.conf'
                    if reader.exists(candidate):
                        acme_paths.append(candidate)
    for engine, paths in (('certbot', certbot_paths), ('acme.sh', acme_paths)):
        tools = [path for path in TOOL_PATHS[engine] if reader.exists(path)]
        if len(tools) != 1:
            if paths:
                failures.append(('', 'missing_tool', engine))
            continue
        tool, tool_identity = tools[0], executable_identity(reader, tools[0])
        for config in paths:
            deployment = ''
            try:
                text = reader.read(config)
                config_hashes = {config: digest(text)}
                if engine == 'certbot':
                    values = ini_values(text)
                    deployment = literal_path(values['fullchain'])
                    if hooks:
                        failures.append((deployment, 'unsupported_hook', engine))
                        continue
                    allowed_top = {'version', 'archive_dir', 'cert', 'privkey', 'chain', 'fullchain'}
                    allowed_params = {'account', 'authenticator', 'server', 'webroot_path', 'key_type', 'pref_challs',
                                      'renew_before_expiry', 'rsa_key_size', 'elliptic_curve', 'reuse_key', 'preferred_chain'}
                    authenticator = values.get('[renewalparams]authenticator')
                    plugin = CERTBOT_DNS.get(authenticator)
                    if plugin:
                        allowed_params.update(plugin[1])
                    if any((key not in allowed_top and not key.startswith('[[webroot_map]]') and
                            (not key.startswith('[renewalparams]') or key[len('[renewalparams]'):] not in allowed_params))
                           for key in values):
                        reason = 'unsupported_hook' if any('hook' in key for key in values) else 'unsupported_config'
                        failures.append((deployment, reason, engine))
                        continue
                    if authenticator != 'webroot' and not plugin:
                        raise ProbeError('unsupported_renewal')
                    order = os.path.basename(config)[:-5]
                    if not SAFE_ORDER.fullmatch(order):
                        raise ProbeError('unsupported_renewal')
                    for name in ('archive_dir', 'cert', 'privkey', 'chain'):
                        literal_path(values[name])
                    private_path = literal_path(values['privkey'])
                    cert_path = literal_path(values['cert'])
                    if cert_path == private_path or deployment == private_path:
                        failures.append((deployment, 'deployment_mismatch', engine))
                        continue
                    order_public = reader.read(cert_path, public=True)
                    deployed_public = reader.read(deployment, public=True)
                    if not order_public or not deployed_public or digest(order_public) != digest(deployed_public):
                        failures.append((deployment, 'deployment_mismatch', engine))
                        continue
                    extra = {}
                    extra['order_public_hash'] = digest(order_public)
                    if authenticator == 'webroot':
                        webroots = values.get('[renewalparams]webroot_path', '').rstrip(',')
                        if not webroots:
                            raise ProbeError('unsupported_renewal')
                        for value in webroots.split(','):
                            literal_path(value.strip())
                        validation = 'webroot（复用已有配置）'
                    else:
                        credential_path = literal_path(values['[renewalparams]' + plugin[1][0]])
                        extra.update({'credential_path': credential_path,
                                      'credential_identity': file_identity(reader, credential_path),
                                      'plugin': certbot_plugin(reader, plugin[0])})
                        validation = authenticator + '（复用已有 DNS 账户）'
                    for key, value in values.items():
                        if key.startswith('[[webroot_map]]'):
                            domain(key[len('[[webroot_map]]'):])
                            literal_path(value)
                    domains = []  # The selected public deployment supplies the frozen SAN set.
                else:
                    values = assignments(text)
                    deployment = literal_path(values['Le_RealFullChainPath'])
                    account_path = '/root/.acme.sh/account.conf'
                    account = assignments(reader.read(account_path)) if reader.exists(account_path) else {}
                    if reader.exists(account_path):
                        config_hashes[account_path] = reader.files[account_path]
                    # Shell substitutions, exports, aliases and unknown global
                    # executable overrides are rejected even if not used today.
                    account_allowed = {'ACCOUNT_EMAIL', 'ACCOUNT_URL', 'ACCOUNT_KEY_PATH', 'ACCOUNT_THUMBPRINT',
                                       'DEFAULT_ACME_SERVER', 'CA_HOME', 'LOG_LEVEL', 'LOG_FILE', 'CERT_HOME',
                                       'SAVED_ACCOUNT_EMAIL', 'ACCOUNT_EAB_KEY_ID', 'ACCOUNT_EAB_HMAC_KEY', 'USER_PATH'}
                    provider = values.get('Le_Webroot', '')
                    dns_keys = ACME_DNS.get(provider, ())
                    account_allowed.update(dns_keys)
                    account_allowed.update('SAVED_' + key for key in dns_keys)
                    if any(key not in account_allowed for key in account):
                        raise ProbeError('unsupported_renewal')
                    # USER_PATH is standard install metadata. acme exports it
                    # only for --cron, which this adapter never invokes; explicit
                    # renew uses our fixed environment and records that PATH.
                    # User-controlled CA/home/key/config overrides can redirect
                    # order/account resolution; support the standard layout only.
                    if any(key in account for key in ('CA_HOME', 'CERT_HOME', 'ACCOUNT_KEY_PATH', 'LOG_FILE')):
                        raise ProbeError('unsupported_renewal')
                    allowed = {'Le_Domain', 'Le_Alt', 'Le_Webroot', 'Le_API', 'Le_Keylength', 'Le_LinkOrder', 'Le_LinkCert',
                               'Le_LinkIssuer', 'Le_CertCreateTime', 'Le_CertCreateTimeStr', 'Le_NextRenewTime', 'Le_NextRenewTimeStr',
                               'Le_RealCertPath', 'Le_RealCACertPath', 'Le_RealKeyPath', 'Le_RealFullChainPath', 'Le_ReloadCmd',
                               'Le_PreHook', 'Le_PostHook', 'Le_RenewHook', 'Le_Vlist', 'Le_OrderFinalize', 'Le_OrderUrl',
                               'Le_DNSAlias', 'Le_ChallengeAlias', 'Le_RetryAfter', 'Le_LocalAddress', 'Le_Listen_V4', 'Le_Listen_V6'}
                    allowed.update(('Le_InstallCertSuccessTime', 'Le_InstallCertSuccessTimeStr'))
                    if any(key not in allowed for key in values):
                        raise ProbeError('unsupported_renewal')
                    if any(values.get(key) for key in ('Le_PreHook', 'Le_PostHook', 'Le_RenewHook',
                                                        'Le_DNSAlias', 'Le_ChallengeAlias', 'Le_LocalAddress')):
                        failures.append((deployment, 'unsupported_hook', engine))
                        continue
                    # Other install locations would also be written by --renew;
                    # only the standard chain+key deployment mapping is supported.
                    if values.get('Le_RealCertPath') or values.get('Le_RealCACertPath'):
                        failures.append((deployment, 'deployment_mismatch', engine))
                        continue
                    private_path = literal_path(values['Le_RealKeyPath'])
                    order = domain(values['Le_Domain'])
                    ecc = values.get('Le_Keylength', '').startswith('ec-') or '/'+order+'_ecc/' in config
                    expected_config = '/root/.acme.sh/' + order + ('_ecc' if ecc else '') + '/' + order + '.conf'
                    if config != expected_config:
                        raise ProbeError('unsupported_renewal')
                    domains = [order] + ([] if values.get('Le_Alt', 'no') == 'no' else
                                         [domain(v) for v in values['Le_Alt'].split(',')])
                    webroot = values.get('Le_Webroot', '')
                    extra = {'ecc': ecc}
                    if webroot in ACME_DNS:
                        plugin_path = '/root/.acme.sh/dnsapi/' + webroot + '.sh'
                        extra['plugin'] = {'path': plugin_path, 'identity': executable_identity(reader, plugin_path)}
                        if not any(account.get(key) for key in (*dns_keys, *('SAVED_' + key for key in dns_keys))):
                            raise ProbeError('unsupported_renewal')
                        validation = webroot + '（复用已有 DNS 账户）'
                    else:
                        for value in webroot.split(','):
                            literal_path(value)
                        validation = 'webroot（复用已有配置）'
                if values.get('Le_API', values.get('[renewalparams]server', '')) not in (
                        'https://acme-v02.api.letsencrypt.org/directory', 'https://acme.zerossl.com/v2/DV90'):
                    raise ProbeError('unsupported_renewal')
                if engine == 'acme.sh':
                    ca_path = '/root/.acme.sh/ca/' + values['Le_API'].split('://', 1)[1]
                    # acme.sh sources CA-specific settings as shell as well. Only
                    # known, literal account metadata is accepted; never ignore a
                    # second executable configuration file.
                    for ca_config in (ca_path + '/ca.conf',):
                        if reader.exists(ca_config):
                            ca_values = assignments(reader.read(ca_config))
                            if any(key not in ('ACCOUNT_URL', 'ACCOUNT_THUMBPRINT', 'CA_EMAIL', 'CA_KEY_HASH',
                                               'CA_EAB_KEY_ID', 'CA_EAB_HMAC_KEY') for key in ca_values):
                                raise ProbeError('unsupported_renewal')
                            config_hashes[ca_config] = reader.files[ca_config]
                related_sites = [site for site in sites if deployment in site['certs']]
                if not related_sites or any(len(site['certs']) != 1 or site['keys'] != [private_path] for site in related_sites):
                    failures.append((deployment, 'deployment_mismatch', engine))
                    continue
                if engine == 'acme.sh' and values.get('Le_ReloadCmd'):
                    services = {site['main'] for site in related_sites}
                    try:
                        if len(services) != 1 or next(iter(services)) not in SERVICES:
                            raise ProbeError('unsupported_renewal')
                        extra['saved_reload'] = known_saved_reload(reader, values['Le_ReloadCmd'], SERVICES[next(iter(services))])
                    except ProbeError:
                        failures.append((deployment, 'unsupported_hook', engine))
                        continue
                candidates.append({'engine': engine, 'tool': tool, 'tool_identity': tool_identity, 'order_config': config,
                                   'config_hashes': config_hashes, 'order': order, 'cert_path': deployment,
                                   'private_path': private_path, 'domains': sorted(set(domains)), 'validation': validation, **extra})
            except (KeyError, ValueError, OSError, ProbeError) as error:
                # Do not expose the offending config/value, which could contain
                # a DNS credential. Discovery remains usable without renew support.
                if isinstance(error, ProbeError) and (str(error).endswith('limit') or str(error) == 'timeout'):
                    raise
                failures.append((deployment, 'unsupported_config', engine))
    if not any(reader.exists(path) for paths in TOOL_PATHS.values() for path in paths):
        failures.append(('', 'missing_tool', ''))
    return candidates, failures


def build_snapshot(reader):
    sites, warnings, mains = [], [], []
    for path in (*NGINX_MAIN, *APACHE_MAIN):
        if reader.exists(path):
            mains.append(path)
            sites.extend(nginx_sites(reader, path) if path in NGINX_MAIN else apache_sites(reader, path))
    # Baota site files are only read directly when no canonical main config
    # references them. No assumption that /www/server is the running instance.
    if not mains:
        for engine in ('nginx', 'apache'):
            for path in reader.matches('/www/server/panel/vhost/' + engine + '/*.conf', config=True):
                sites.extend(nginx_sites(reader, path) if engine == 'nginx' else apache_sites(reader, path))
        warnings.append('未找到标准 Web 服务主配置；只能展示固定宝塔站点目录，续签不可用')
    if not mains and not sites:
        warnings.append('未找到受支持的 Nginx/Apache 配置；容器及自定义配置目录未覆盖')
    items = []
    for index, site in enumerate(sites):
        names = []
        for value in site['domains']:
            try:
                names.append(domain(value))
            except (ValueError, UnicodeError):
                warnings.append('忽略无法握手的默认、正则或动态网站域名')
        for cert_path in site['certs'] or ['']:
            public = ''
            if cert_path:
                try:
                    public = reader.read(cert_path, public=True)
                    if not public:
                        warnings.append('部署路径未提供可识别的公开证书')
                except ProbeError as error:
                    if str(error).endswith('limit') or str(error) == 'timeout':
                        raise
                    warnings.append('部署公开证书无法读取或解析')
            for name in sorted(set(names)):
                for port in sorted(set(site['ports'])):
                    if len(items) >= MAX_ITEMS:
                        raise ProbeError('item_limit')
                    items.append({'domain': name, 'port': port, 'site_id': site['main'] + '#' + str(index),
                                  'site_name': names[0] if names else '', 'cert_path': cert_path, 'public_pem': public,
                                  '_main': site['main'], '_server': site['server'], 'renewal': unsupported()})
    candidates, failures = order_plans(reader, sites)
    for item in items:
        matches = [plan for plan in candidates if plan['cert_path'] == item['cert_path']]
        rejected = [entry for entry in failures if entry[0] == item['cert_path'] or not entry[0]]
        if len(matches) > 1 or (matches and any(entry[0] == item['cert_path'] for entry in rejected)):
            item['renewal'] = unsupported('ambiguous_order')
            continue
        if not matches:
            if rejected:
                item['renewal'] = unsupported(rejected[0][1], rejected[0][2])
            continue
        plan = dict(matches[0])
        service = SERVICES.get(item['_main'])
        if not service or not reader.exists(service[1]):
            item['renewal'] = unsupported('unverified_service', plan['engine'])
            continue
        configured_sites = [site for site in sites if site['main'] == item['_main']]
        try:
            if any(not site.get('reload_safe') for site in configured_sites):
                raise ProbeError('unsupported_renewal')
            runtime = runtime_service(reader, service, configured_sites[0].get('pid_paths', []))
        except (ProbeError, OSError, ValueError, IndexError):
            item['renewal'] = unsupported('unverified_service', plan['engine'])
            continue
        # Every SSL site using this cert belongs in the frozen reload impact.
        related = [value for value in items if value['cert_path'] == item['cert_path']]
        if any(value['_main'] != item['_main'] for value in related):
            item['renewal'] = unsupported('unverified_service', plan['engine'])
            continue
        deployments = sorted({(v['site_id'], v['domain'], v['port'], v['cert_path']) for v in related})
        plan['version'] = 1
        plan['service'] = {'kind': service[0], 'tool': service[1], 'main': service[2],
                           'identity': executable_identity(reader, service[1]), 'runtime': runtime}
        plan['site_mapping'] = [list(v) for v in deployments]
        plan['web_config_hashes'] = {path: value for path, value in reader.files.items()
                                     if path in CONFIG_FILES or any(path.startswith(root + '/') for root in CONFIG_ROOTS)}
        plan['before_public_hash'] = digest(item['public_pem'])
        plan['summary'] = {'engine': plan['engine'], 'domains': sorted({v['domain'] for v in related}),
                           'domain': item['domain'], 'cert_path': item['cert_path'], 'validation': plan['validation'],
                           'reload': service[0] + ': 配置检查后平滑重载',
                           'impacts': ['仅所选既有订单', '该证书关联站点', '已识别 Web 服务平滑重载']}
        if plan.get('saved_reload'):
            plan['summary']['reload'] += '（既有订单同样执行固定配置检查及重载）'
        item['renewal'] = {'engine': plan['engine'], 'supported': True, 'reason_code': '', 'reason': '',
                           'summary': plan['summary'], 'plan': plan}
    for item in items:
        item.pop('_main')
        item.pop('_server')
    return {'items': items, 'warnings': sorted(set(warnings))}


def scan(root='/'):
    return build_snapshot(Reader(root))


def matching_plan(snapshot, plan):
    # Plans are never trusted command input. Rebuild from live files and compare
    # the full server-side frozen plan before selecting any argv.
    if not isinstance(plan, dict):
        raise ProbeError('stale_plan')
    # The SSH probe cannot parse X509 without adding a remote dependency. This
    # single derived public field is bound to before_public_hash by the trusted
    # local cryptography parser and never influences remote argv or paths.
    comparable = {key: value for key, value in plan.items() if key != 'public_baseline'}
    for item in snapshot['items']:
        current = item['renewal'].get('plan')
        if current == comparable:
            return current
    raise ProbeError('stale_plan')


def observe(plan, root='/'):
    if not isinstance(plan, dict) or plan.get('version') != 1:
        raise ProbeError('stale_plan')
    reader = Reader(root)
    path = literal_path(plan.get('cert_path'))
    return {'certificates': [{'cert_path': path, 'public_pem': reader.read(path, public=True)}]}


def run_command(argv, timeout, env, **kwargs):
    """Own the complete tool process group, including DNS hook children."""
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             shell=False, start_new_session=True, env=env)
    try:
        child.wait(timeout=timeout)
    except BaseException:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        raise
    return child


def renew(plan, root='/', runner=None):
    started = time.monotonic()
    runner = runner or run_command
    try:
        current = matching_plan(scan(root), plan)
    except ProbeError:
        raise ProbeError('stale_plan') from None
    reader = Reader(root)
    # Every command below is an argv array built from a fresh recognized plan.
    if current['engine'] == 'certbot':
        args = [current['tool'], 'renew', '--cert-name', current['order'], '--non-interactive', '--no-directory-hooks',
                '--config', '/dev/null', '--config-dir', '/etc/letsencrypt', '--work-dir', '/var/lib/letsencrypt',
                '--logs-dir', '/var/log/letsencrypt', '--no-random-sleep-on-renew']
    elif current['engine'] == 'acme.sh':
        args = [current['tool'], '--renew', '-d', current['order'], '--home', '/root/.acme.sh',
                '--config-home', '/root/.acme.sh', '--cert-home', '/root/.acme.sh']
        if current.get('ecc'):
            args.append('--ecc')
    else:
        raise ProbeError('unsupported_renewal')
    def invoke(argv):
        remaining = RENEW_TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            raise ProbeError('renewal_uncertain')
        try:
            result = runner(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            shell=False, timeout=remaining, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                                                'HOME': '/root', 'LANG': 'C.UTF-8'})
        except (OSError, subprocess.TimeoutExpired):
            raise ProbeError('renewal_uncertain') from None
        if result.returncode != 0:
            raise ProbeError('renewal_uncertain')
    invoke(args)
    observed = observe(current, root)
    changed = digest(observed['certificates'][0]['public_pem']) != current['before_public_hash']
    if not changed:
        return {'state': 'unchanged', 'changed': False, 'message': '客户端未更新部署证书；可能尚未达到续签窗口', **observed}
    service = current['service']
    if executable_identity(reader, service['tool']) != service['identity']:
        raise ProbeError('renewal_uncertain')
    observed_runtime = runtime_service(reader, (service['kind'], service['tool'], service['main']),
                                       [service['runtime']['pid_path']])
    if observed_runtime != service['runtime']:
        raise ProbeError('renewal_uncertain')
    for path, expected in current['web_config_hashes'].items():
        if digest(reader.read(path)) != expected:
            raise ProbeError('renewal_uncertain')
    if service['kind'] == 'nginx':
        invoke([service['tool'], '-t', '-c', service['main']])
        invoke([service['tool'], '-s', 'reload', '-c', service['main']])
    else:
        invoke([service['tool'], '-t', '-f', service['main']])
        invoke([service['tool'], '-k', 'graceful', '-f', service['main']])
    return {'state': 'success', 'changed': True, 'message': '部署证书已更新，已知 Web 服务配置检查及重载成功', **observed}


def main():
    try:
        # This probe is shipped as static source. Only bounded data is accepted on
        # stdin; no frontend-provided command, source path or executable is used.
        wire = sys.stdin.buffer.read(MAX_OUTPUT_BYTES + 1)
        if len(wire) > MAX_OUTPUT_BYTES:
            raise ProbeError('output_limit')
        payload = json.loads(wire or '{}')
        action = payload.get('action', 'discover')
        if action == 'discover':
            result = scan()
        elif action == 'renew':
            result = renew(payload.get('plan'))
        elif action == 'verify':
            result = observe(payload.get('plan'))
        else:
            raise ProbeError('unsupported')
        output = json.dumps({'result': result}, separators=(',', ':'), ensure_ascii=True)
        if len(output.encode('ascii')) > MAX_OUTPUT_BYTES:
            raise ProbeError('output_limit')
    except ProbeError as error:
        code = str(error)
        known = ('timeout', 'output_limit', 'stale_plan', 'unsupported_renewal', 'renewal_uncertain')
        output = json.dumps({'error': code if code in known else 'unsupported'})
    except Exception:
        output = '{"error":"unsupported"}'
    sys.stdout.write(output)


if __name__ == '__main__':
    main()
