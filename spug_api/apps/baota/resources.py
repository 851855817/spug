"""Bounded target-panel readers. Never expose transport responses to the browser."""
import re
import posixpath

from .client import PanelAPIError
from .validation import is_linux_system, positive_id


# Official update-package protocol inspection; runtime evidence is recorded separately.
SUPPORTED_VERSIONS = frozenset(('11.7.0', '13.1.0'))
MAX_RESOURCES = 5000
PAGE_SIZE = 100
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}\Z')


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError('软件、版本或扩展标识格式无效')
    return value


def system_info(client):
    data = client.request('/system', 'GetSystemTotal')
    if not isinstance(data, dict) or not is_linux_system(data):
        raise PanelAPIError('仅支持已识别的 Linux 面板')
    version = data.get('version')
    if not isinstance(version, str) or version not in SUPPORTED_VERSIONS:
        raise PanelAPIError('该面板版本尚未经过协议适配，禁止变更')
    system = data.get('system', '')
    if not isinstance(system, str) or len(system) > 200:
        raise PanelAPIError('系统类型字段格式无效')
    return {'version': version, 'system': system,
            'cpu_cores': data.get('cpuNum') if type(data.get('cpuNum')) is int and 0 < data['cpuNum'] < 100000 else None,
            'memory_mb': data.get('memTotal') if type(data.get('memTotal')) is int and 0 < data['memTotal'] < 10**12 else None,
            'compatibility': 'official_source_reviewed'}


def table(client, name):
    if name not in ('sites', 'domain', 'tasks', 'databases'):
        raise ValueError('不允许查询该资源')
    rows, seen = [], set()
    for page in range(1, MAX_RESOURCES // PAGE_SIZE + 2):
        response = client.request('/data', 'getData', {'table': name, 'p': page, 'limit': PAGE_SIZE})
        data = response.get('data') if isinstance(response, dict) else None
        if not isinstance(data, list) or len(data) > PAGE_SIZE:
            raise PanelAPIError('面板资源分页格式无效')
        for row in data:
            if not isinstance(row, dict):
                raise PanelAPIError('面板资源格式无效')
            rid = positive_id(row.get('id'))
            if rid in seen:
                raise PanelAPIError('面板资源分页重复，请重新预检')
            seen.add(rid)
            rows.append(row)
        if len(rows) > MAX_RESOURCES:
            raise PanelAPIError('面板资源数量超出单次任务限制')
        if len(data) < PAGE_SIZE:
            return rows
    raise PanelAPIError('面板资源分页超出限制')


def php_branch(value):
    if isinstance(value, dict):
        value = value.get('version', value.get('php_version'))
    if value in ('静态', 'static', '00'):
        return '00'
    if isinstance(value, str) and re.fullmatch(r'[5-9](?:\.)?[0-9]', value):
        return value.replace('.', '')
    return ''


def sites(client):
    result = []
    for row in table(client, 'sites'):
        name, path = row.get('name'), row.get('path')
        project, status = row.get('project_type'), row.get('status')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', name)
                or not isinstance(path, str) or len(path) > 512 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path)
                or '..' in path.split('/')
                or not isinstance(project, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', project)
                or type(status) not in (int, str) or str(status) not in ('0', '1')):
            raise PanelAPIError('网站身份字段格式无效')
        result.append({'id': positive_id(row['id']), 'name': name, 'path': path.rstrip('/') or '/',
                       'project_type': project,
                       'php_version': php_branch(row.get('php_version')),
                       'status': str(status)})
    return result


def require_site_root(client, site):
    """Fail before official run-path readers can mkdir an already missing root.

    get_file_attribute is readonly in both reviewed versions, but invokes lsattr
    with its path: validate server data before sending it, not only browser input.
    An administrator concurrently deleting the root between these API calls is
    outside the atomicity available in Baota's API; no existence check can remove
    that server-side race without replacing the underlying run-path protocol.
    """
    path = site.get('path')
    if (not isinstance(path, str) or len(path) > 512 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path)
            or '..' in path.split('/') or posixpath.normpath(path) != path):
        raise PanelAPIError('网站根目录格式无效')
    data = client.request('/files', 'get_file_attribute', {'filename': path})
    if (not isinstance(data, dict) or data.get('is_dir') is not True or data.get('is_link') is not False
            or data.get('name') != posixpath.basename(path) or data.get('path') != posixpath.dirname(path)):
        raise PanelAPIError('网站根目录不存在或无法核实；只读预检不会创建目录')


def domains(client, site_id):
    result = []
    for row in table(client, 'domain'):
        if str(row.get('pid')) == str(site_id):
            name = row.get('name')
            if not isinstance(name, str) or len(name) > 253:
                raise PanelAPIError('网站域名格式无效')
            port = row.get('port', 80)
            if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
                raise PanelAPIError('网站域名端口无效')
            result.append({'name': name.lower(), 'port': int(port)})
    return sorted(result, key=lambda d: (d['name'], d['port']))


def webserver(client):
    data = client.request('/system', 'GetConcifInfo')
    value = data.get('webserver') if isinstance(data, dict) else None
    if value not in ('nginx', 'apache', 'openlitespeed'):
        raise PanelAPIError('无法识别网站服务器类型')
    return value


def php_versions(client):
    data = client.request('/site', 'GetPHPVersion')
    if not isinstance(data, list) or len(data) > 100:
        raise PanelAPIError('PHP 版本列表格式无效')
    return [{'version': php_branch(r.get('version'))} for r in data
            if isinstance(r, dict) and php_branch(r.get('version'))]


def software_find(client, name):
    token(name)
    data = client.request('/plugin', 'get_soft_find', {'sName': name}, allow_status_false=True)
    if not isinstance(data, dict) or data.get('name') != name or not isinstance(data.get('versions'), list):
        raise PanelAPIError('软件不在当前面板目录中')
    return data  # Internal only. Callers MUST select fields before storing/returning.


def software_option(data):
    name = token(data.get('name'))
    versions = []
    for row in data.get('versions', [])[:100]:
        if not isinstance(row, dict):
            continue
        try:
            major, minor = token(row.get('m_version')), token(row.get('version'))
        except ValueError:
            continue
        versions.append({'m_version': major, 'min_version': minor,
                         **{k: row[k] for k in ('mem_limit', 'cpu_limit', 'os_limit')
                            if type(row.get(k)) in (int, float) and 0 <= row[k] <= 10**9}})
    installed = data.get('version', '')
    if not isinstance(installed, str) or (installed and not TOKEN.fullmatch(installed)):
        raise PanelAPIError('软件实际版本格式无效')
    return {'name': name, 'title': name, 'installed': data.get('setup') is True,
            'installed_version': installed, 'versions': versions}


def software_catalog(client):
    rows, seen = [], set()
    for page in range(1, 52):
        response = client.request('/plugin', 'get_soft_list', {'p': page, 'row': PAGE_SIZE, 'type': '0'})
        data = response.get('list', {}).get('data') if isinstance(response, dict) and isinstance(response.get('list'), dict) else None
        if not isinstance(data, list) or len(data) > PAGE_SIZE:
            raise PanelAPIError('软件目录分页格式无效')
        for row in data:
            if not isinstance(row, dict):
                raise PanelAPIError('软件目录格式无效')
            try:
                option = software_option(row)
            except ValueError:
                continue
            if option['name'] in seen:
                raise PanelAPIError('软件目录分页重复')
            seen.add(option['name'])
            rows.append(option)
        if len(rows) > MAX_RESOURCES:
            raise PanelAPIError('软件目录超出限制')
        if len(data) < PAGE_SIZE:
            return rows
    raise PanelAPIError('软件目录分页超出限制')


def extension_config(client, branch):
    if php_branch(branch) != branch or branch == '00':
        raise ValueError('PHP 分支格式无效')
    data = client.request('/ajax', 'GetPHPConfig', {'version': branch})
    libs = data.get('libs') if isinstance(data, dict) else None
    if not isinstance(libs, list) or len(libs) > 500:
        raise PanelAPIError('PHP 扩展目录格式无效')
    return libs


def extension_options(client, branch):
    result = []
    for lib in extension_config(client, branch):
        if not isinstance(lib, dict):
            continue
        try:
            name = token(lib.get('name'))
        except ValueError:
            continue
        supported = lib.get('phpversions')
        if isinstance(supported, str):
            supported = supported.split(',')
        if not isinstance(supported, list) or branch not in [str(v).replace('.', '') for v in supported]:
            continue
        result.append({'name': name, 'configured': lib.get('status') is True})
    return result


def get_resources(client, php_version=None, site_id=None):
    info = system_info(client)
    result = {'compatibility': info, 'sites': sites(client), 'php_versions': php_versions(client),
              'webserver': webserver(client), 'software': software_catalog(client)}
    if php_version is not None:
        result['php_extensions'] = extension_options(client, php_version)
    if site_id is not None:
        result['site_options'] = site_options(client, site_id)
    return result


def get_upgrade_resources(client):
    from .operations.upgrade import UpgradePanel, version_tuple
    info = system_info(client)
    return {'current_version': info['version'], 'versions': sorted(
        (v for v in UpgradePanel.offered(client) if v in SUPPORTED_VERSIONS
         and version_tuple(v) > version_tuple(info['version'])), key=version_tuple)}


def site_options(client, site_id):
    from .operations.certificates import ssl_snapshot
    from .operations.sites import Rewrite
    site_id = positive_id(site_id)
    site = next((s for s in sites(client) if s['id'] == site_id), None)
    if site is None:
        raise ValueError('网站不属于当前面板')
    server = webserver(client)
    options = {'id': site_id, 'domains': domains(client, site_id), 'rewrite_templates': [],
               'webserver': server, 'rewrite_available': False}
    if site['project_type'] == 'PHP' and server in ('nginx', 'apache'):
        options['ssl'] = ssl_snapshot(client, site)
        try:
            path, templates = Rewrite.resolve(client, site, server)
            Rewrite.file_hash(client, path)
        except (PanelAPIError, ValueError):
            options['rewrite_message'] = '该网站伪静态路径尚未完成适配或无法核实'
            return options
        options['rewrite_available'] = True
        for value in templates:
            if not isinstance(value, dict): continue
            try:
                name = token(value.get('name'))
            except ValueError:
                continue
            expected = '/www/server/panel/rewrite/' + server + '/' + name + '.conf'
            if value.get('file') == expected:
                options['rewrite_templates'].append(name)
    return options


def get_resource(client, kind, *, site_id=None, php_version=None):
    """Resource-view contract: fixed kind plus only the relevant optional selector.

    sites -> {compatibility,sites}; software -> {compatibility,software};
    php_versions -> {compatibility,php_versions}; webserver -> {compatibility,webserver};
    php_extensions requires php_version -> {compatibility,php_extensions};
    site_options requires site_id -> {compatibility,site_options:{id,domains,
      rewrite_templates:[NAMES ONLY],rewrite_available,webserver,
      ssl:{enabled,certificate,index,auth_type,type}}};
    upgrade_versions -> {compatibility,current_version,versions:[offered supported targets]}.
    No request can submit a file path, arbitrary action, or read another panel's site.
    Rewrite discovery uses only singular GetRewriteList and readonly get_file_hash;
    missing files remain missing and are reported as unavailable.
    """
    kinds = ('sites', 'software', 'php_versions', 'webserver', 'php_extensions', 'site_options', 'upgrade_versions')
    if kind not in kinds or (site_id is not None) != (kind == 'site_options') or (php_version is not None) != (kind == 'php_extensions'):
        raise ValueError('资源类型或选择参数无效')
    info = system_info(client)
    if kind == 'upgrade_versions':
        return {'compatibility': info, **get_upgrade_resources(client)}
    readers = {'sites': lambda: sites(client), 'software': lambda: software_catalog(client),
               'php_versions': lambda: php_versions(client), 'webserver': lambda: webserver(client),
               'php_extensions': lambda: extension_options(client, php_version),
               'site_options': lambda: site_options(client, site_id)}
    return {'compatibility': info, kind: readers[kind]()}
