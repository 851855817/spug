import json
import posixpath
import re

from .. import resources as r
from ..client import PanelAPIError
from .base import (Operation, boolean, child, digest, file_body, identity, preview,
                   result, selected_sites, selection, strict, MAX_CHILDREN)


def domain(value):
    if not isinstance(value, str) or len(value) > 253 or value != value.strip():
        raise ValueError('域名格式无效')
    try:
        value = value.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ValueError('域名格式无效') from None
    if (len(value) > 253 or '.' not in value or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',
            label) for label in value.split('.')) or value.split('.')[-1].isdigit()):
        raise ValueError('请填写不含端口或通配符的 DNS 域名')
    return value


def domain_list(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise ValueError('域名数量无效')
    names = [domain(v) for v in values]
    if len(set(names)) != len(names):
        raise ValueError('域名不能重复')
    return names


def site_path(value):
    if (not isinstance(value, str) or len(value) > 512 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)
            or '..' in value.split('/') or posixpath.normpath(value) != value or value.endswith('.')):
        raise ValueError('网站目录格式无效')
    roots = ('/www/wwwroot/', '/var/www/', '/srv/www/', '/srv/http/', '/home/')
    if not any(value.startswith(root) and len(value) > len(root) for root in roots):
        raise ValueError('网站目录必须位于允许的网站数据目录内')
    if value.startswith('/home/') and len(value.split('/')) < 4:
        raise ValueError('不能将用户主目录作为网站目录')
    return value


def site_preflight(client, params, info, prepare):
    items = []
    for site in selected_sites(client, params):
        item = child('site:' + str(site['id']), site['name'], {'id': site['id'], 'name': site['name']}, info,
                     before={'identity': identity(site)})
        if site.get('missing'):
            item.update(state='failed', message='所选网站不存在于当前面板')
        elif site['project_type'] != 'PHP':
            item.update(state='skipped', message='当前适配器只支持 PHP/静态网站')
        else:
            try:
                prepare(site, item)
            except (PanelAPIError, ValueError):
                item.update(state='failed', message='无法核对该网站的目标配置')
        items.append(item)
    return preview(items)


class AddSite(Operation):
    code, category, label = 'site_add', 'site_add', '添加网站'
    resources = ('sites', 'php_versions')

    def validate(self, params):
        strict(params, ('entries',), ('entries',))
        entries = params['entries']
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CHILDREN:
            raise ValueError('网站数量无效')
        clean, used = [], set()
        for entry in entries:
            strict(entry, ('domains', 'path', 'php_version', 'port', 'remark'), ('domains', 'path', 'php_version'))
            names = domain_list(entry['domains'])
            port = entry.get('port', 80)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError('网站端口无效')
            version = entry['php_version']
            if not isinstance(version, str) or not re.fullmatch(r'(?:00|[5-9][0-9])', version):
                raise ValueError('PHP 版本格式无效')
            remark = entry.get('remark', '')
            if not isinstance(remark, str) or len(remark) > 200 or re.search(r'[\x00-\x1f\x7f]', remark):
                raise ValueError('网站备注格式无效')
            if any((name, port) in used for name in names):
                raise ValueError('待添加的网站域名端口不能重复')
            used.update((name, port) for name in names)
            clean.append({'domains': names, 'path': site_path(entry['path']), 'php_version': version,
                          'port': port, 'remark': remark})
        if len({e['path'] for e in clean}) != len(clean):
            raise ValueError('待添加的网站目录不能重复')
        return {'entries': clean}

    @staticmethod
    def assess(client, entry):
        sites = r.sites(client)
        bound = r.table(client, 'domain')
        matches = [s for s in sites if s['name'] == entry['domains'][0]]
        if matches:
            s = matches[0]
            names = r.domains(client, s['id'])
            wanted = sorted([{'name': n, 'port': entry['port']} for n in entry['domains']], key=lambda d: (d['name'], d['port']))
            if (s['path'] == entry['path'] and s['php_version'] == entry['php_version']
                    and s['project_type'] == 'PHP' and names == wanted):
                return 'already', s['id']
            raise PanelAPIError('已存在同名但配置不同的网站')
        if any(s['path'] == entry['path'] for s in sites):
            raise PanelAPIError('网站目录已被占用')
        if any(b.get('name') in entry['domains'] and str(b.get('port')) == str(entry['port']) for b in bound):
            raise PanelAPIError('域名端口已被占用')
        if entry['php_version'] not in [v['version'] for v in r.php_versions(client)]:
            raise PanelAPIError('目标 PHP 分支未安装')
        return 'ready', None

    def preflight(self, client, params):
        params = self.validate(params)
        info, items = r.system_info(client), []
        for entry in params['entries']:
            item = child('new-site:' + entry['domains'][0] + ':' + str(entry['port']), entry['domains'][0],
                         {'name': entry['domains'][0]}, info, plan=entry)
            try:
                state, sid = self.assess(client, entry)
                item.update(state=state, message='配置已符合目标' if state == 'already' else '预检通过')
                if sid: item['before']['existing_id'] = sid
            except PanelAPIError:
                item.update(state='failed', message='域名、目录或 PHP 分支存在冲突')
            items.append(item)
        return preview(items)

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        self.guard(client, item)
        entry = item['plan']
        state, _ = self.assess(client, entry)
        if state == 'already':
            return result('already', '网站配置已符合目标')
        names = entry['domains']
        extra = [n + ':' + str(entry['port']) if entry['port'] != 80 else n for n in names[1:]]
        client.request('/site', 'AddSite', {'webname': json.dumps({'domain': names[0], 'domainlist': extra,
                       'count': len(names) - 1}), 'path': entry['path'], 'type': 'PHP',
                       'version': entry['php_version'], 'port': str(entry['port']), 'type_id': 0,
                       'ps': entry['remark'], 'ftp': 'false', 'sql': 'false'}, mutating=True)
        return self.reconcile(client, item, {'phase': 'apply'})

    def _reconcile(self, client, item, remote):
        state, sid = self.assess(client, item['plan'])
        if state == 'already':
            return result('success', '网站及全部域名、目录、PHP 版本已读回确认', {'site_id': sid})
        return result('remote', '尚未读到完整网站配置，不会自动重发', remote=remote)


class Basedir(Operation):
    code, category, label = 'site_basedir', 'site_basedir', '防跨站设置'
    resources = ('sites',)

    def validate(self, params):
        strict(params, ('sites', 'all_sites', 'enabled'), ('enabled',))
        return {**selection(params), 'enabled': boolean(params['enabled'])}

    @staticmethod
    def snapshot(client, site):
        data = client.request('/site', 'GetDirUserINI', {'id': site['id'], 'path': site['path']})
        if not isinstance(data, dict) or type(data.get('userini')) is not bool:
            raise PanelAPIError('防跨站读回格式无效')
        run = data.get('runPath', {})
        path = run.get('runPath') if isinstance(run, dict) else run
        if not isinstance(path, str) or len(path) > 512:
            raise PanelAPIError('运行目录读回格式无效')
        return {'enabled': data['userini'], 'run_path': path}

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        server = r.webserver(client)
        def prepare(site, item):
            if server == 'openlitespeed':
                item.update(state='skipped', message='OpenLiteSpeed 尚未完成此操作适配')
                return
            before = self.snapshot(client, site)
            item['before'].update(config=before, webserver=server)
            item['plan'] = {'enabled': params['enabled']}
            if before['enabled'] == params['enabled']:
                item.update(state='already', message='防跨站设置已符合目标')
        return site_preflight(client, params, info, prepare)

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        site = self.guard(client, item, site=True)
        if (r.webserver(client) != item['before']['webserver']
                or self.snapshot(client, site) != item['before']['config']):
            raise PanelAPIError('防跨站配置已变化')
        client.request('/site', 'multiple_basedir', {'site_ids': json.dumps([site['id']]),
                       'stat': 'open' if item['plan']['enabled'] else 'close'}, mutating=True)
        return self.reconcile(client, item, {'phase': 'apply'})

    def _reconcile(self, client, item, remote):
        site = self.guard(client, item, site=True)
        current = self.snapshot(client, site)
        if current['enabled'] == item['plan']['enabled']:
            return result('success', '防跨站实际配置已读回确认', current)
        if remote.get('ambiguous'):
            return result('uncertain', '防跨站尚未符合目标，仅允许继续核验', current, remote)
        return result('failed', '面板返回后实际防跨站配置未符合目标', current)


class Rewrite(Operation):
    code, category, label = 'site_rewrite', 'site_rewrite', '伪静态设置'
    resources = ('sites', 'rewrite_templates', 'webserver')

    def validate(self, params):
        strict(params, ('sites', 'all_sites', 'mode', 'webserver', 'rules', 'template'), ('mode', 'webserver'))
        mode, server = params['mode'], params['webserver']
        if mode not in ('custom', 'template', 'clear') or server not in ('nginx', 'apache'):
            raise ValueError('伪静态模式或服务器类型无效')
        clean = {**selection(params), 'mode': mode, 'webserver': server}
        if mode == 'custom':
            if 'template' in params: raise ValueError('自定义规则不能指定模板')
            value = params.get('rules')
            if not isinstance(value, str) or len(value.encode('utf-8')) > 65536 or '\x00' in value:
                raise ValueError('伪静态规则格式或长度无效')
            clean['rules'] = value.replace('\r\n', '\n')
        elif mode == 'template':
            if 'rules' in params: raise ValueError('模板模式不能指定自定义规则')
            clean['template'] = r.token(params.get('template'))
        elif 'rules' in params or 'template' in params:
            raise ValueError('清空模式包含多余参数')
        return clean

    @staticmethod
    def expected_file(client, site, server):
        if server == 'nginx':
            return '/www/server/panel/vhost/rewrite/' + site['name'] + '.conf'
        if server == 'apache':
            run = client.request('/site', 'GetSiteRunPath', {'id': site['id']}).get('runPath')
            if (not isinstance(run, str) or len(run) > 512 or not re.fullmatch(r'/[A-Za-z0-9_./-]*', run)
                    or '..' in run.split('/')):
                raise PanelAPIError('运行目录无效')
            # The official resolver concatenates '/public' + '.htaccess', producing an
            # inactive sibling file. Reject nested Apache roots until a dedicated adapter
            # verifies DocumentRoot and AllowOverride; a matching digest is insufficient.
            if run != '/':
                raise PanelAPIError('Apache 子目录运行路径尚未完成生效配置适配')
            return site['path'].rstrip('/') + '/.htaccess'
        raise PanelAPIError('网站服务器类型尚未完成伪静态适配')

    @staticmethod
    def resolve(client, site, server):
        # Reject unsupported Apache roots BEFORE calling the official resolver, because
        # that resolver would create the inactive public.htaccess file as a side effect.
        expected = Rewrite.expected_file(client, site, server)
        # Official resolver may create a missing empty rewrite file; documented cache-like side effect.
        data = client.request('/site', 'GetRewriteLists', {'site_ids': json.dumps([site['id']]), 'site_type': 'PHP'})
        if not isinstance(data, dict): raise PanelAPIError('伪静态文件解析失败')
        matches = [v for v in data.get('site_rewrites', []) if isinstance(v, dict) and v.get('id') == site['id']
                   and v.get('name') == site['name']]
        if len(matches) != 1: raise PanelAPIError('伪静态文件归属不明')
        if matches[0].get('file') != expected:
            raise PanelAPIError('伪静态文件不在已验证的网站路径内')
        return expected, data.get('template_rewrites', [])

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        server = r.webserver(client)
        def prepare(site, item):
            if server != params['webserver']:
                item.update(state='skipped', message='网站服务器类型与规则类型不符')
                return
            path, templates = self.resolve(client, site, server)
            body = file_body(client, path)
            rules = params.get('rules', '')
            if params['mode'] == 'template':
                name = params['template']
                expected = '/www/server/panel/rewrite/' + server + '/' + name + '.conf'
                matches = [t for t in templates if isinstance(t, dict) and t.get('name') == name and t.get('file') == expected]
                if len(matches) != 1: raise PanelAPIError('模板不在当前服务器目录中')
                rules = file_body(client, expected)
                if len(rules.encode('utf-8')) > 65536: raise PanelAPIError('模板超出限制')
            item['before'].update(rewrite_digest=digest(body), webserver=server)
            item['plan'] = {'file': path, 'rules': rules, 'digest': digest(rules)}
            if body == rules: item.update(state='already', message='伪静态规则已符合目标')
        return site_preflight(client, params, info, prepare)

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        site = self.guard(client, item, site=True)
        if r.webserver(client) != item['before']['webserver']:
            raise PanelAPIError('网站服务器类型已变化')
        if self.expected_file(client, site, item['before']['webserver']) != item['plan']['file']:
            raise PanelAPIError('伪静态文件或运行目录已变化')
        if digest(file_body(client, item['plan']['file'])) != item['before']['rewrite_digest']:
            raise PanelAPIError('伪静态内容已变化')
        response = client.request('/site', 'SetRewriteLists', {'sites': json.dumps([{
            'id': item['target']['id'], 'name': item['target']['name'], 'file': item['plan']['file']}]),
            'rewrite_data': item['plan']['rules']}, mutating=True)
        rejected = not (isinstance(response, list) and len(response) == 1 and isinstance(response[0], dict)
                        and response[0].get('id') == item['target']['id'] and response[0].get('status') is True)
        return self.reconcile(client, item, {'phase': 'apply', 'rejected': rejected})

    def _reconcile(self, client, item, remote):
        site = self.guard(client, item, site=True)
        if (r.webserver(client) != item['before']['webserver'] or
                self.expected_file(client, site, item['before']['webserver']) != item['plan']['file']):
            raise PanelAPIError('伪静态生效文件或运行目录已变化')
        actual = digest(file_body(client, item['plan']['file']))
        if remote.get('rejected'):
            restored = actual == item['before']['rewrite_digest']
            return result('failed', 'Web 配置检查未通过，已核对面板回滚结果', {'rollback_verified': restored, 'digest': actual})
        if actual == item['plan']['digest']:
            return result('success', '伪静态规则已读回确认', {'digest': actual})
        return result('uncertain' if remote.get('ambiguous') else 'failed',
                      '伪静态内容未符合目标，不会自动重发或恢复', {'digest': actual}, remote)
