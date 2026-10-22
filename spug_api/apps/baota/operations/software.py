import json
import re

from .. import resources as r
from ..client import PanelAPIError
from .base import Operation, child, digest, preview, result, strict, MAX_CHILDREN


def install_type(value):
    if value not in ('0', '1'):
        raise ValueError('安装方式只能选择编译或极速安装')
    return value


def tasks(client):
    return [{'id': int(v['id']), 'name': str(v.get('name', ''))[:200], 'status': str(v.get('status', ''))[:10]}
            for v in r.table(client, 'tasks')]


def matching(task, name, versions=None):
    # Parse exact bracket target; prefixes would confuse nginx and nginx-related plugins.
    match = re.fullmatch(r'(?:安装|升级|卸载|upgrade)\[([A-Za-z0-9_.+-]+)\]', task['name'])
    if not match: return False
    target = match.group(1)
    if not target.startswith(name + '-'): return False
    return versions is None or target[len(name) + 1:] in versions


def queue_snapshot(client, name):
    values = tasks(client)
    if any(matching(v, name) and v['status'] != '1' for v in values):
        raise PanelAPIError('目标软件已有运行中的安装任务')
    return [v['id'] for v in values]


def queue_state(client, item, remote, name, versions):
    values = tasks(client)
    old = set(item['before']['task_ids'])
    candidates = [v for v in values if v['id'] not in old and matching(v, name, versions)]
    known = set(remote.get('task_ids', []))
    if known:
        candidates = [v for v in candidates if v['id'] in known]
    # Multiple indistinguishable new tasks might include a human's request: do not claim one.
    if len(candidates) > 1:
        return {'phase': 'apply', 'task_ids': [], 'collision': True}, 'collision'
    output = {**remote, 'phase': 'apply', 'task_ids': [v['id'] for v in candidates]}
    return output, ('terminal' if candidates and all(v['status'] == '1' for v in candidates) else 'pending')


def software_snapshot(data):
    option = r.software_option(data)
    # Hash only relevant catalog/constraints. No cloud credentials or raw catalog are persisted.
    constraints = {k: data.get(k) for k in ('name', 'versions', 'dependnet', 'mutex', 'type', 'price', 'endtime', 'is_buy')}
    option['catalog_digest'] = digest(json.dumps(constraints, sort_keys=True, ensure_ascii=True, separators=(',', ':')))
    return option


def dependency_groups(value):
    if value in (None, '', [], -1): return []
    if isinstance(value, str) and len(value) <= 2048:
        groups = [part.split('|') for part in value.split(',') if part]
    elif isinstance(value, dict) and set(value) <= {'need', 'selected'}:
        need, selected = value.get('need', []), value.get('selected', [])
        if not isinstance(need, list) or not isinstance(selected, list):
            raise PanelAPIError('软件依赖目录格式无效')
        groups = [[v] for v in need] + selected
    else:
        raise PanelAPIError('软件依赖目录格式无效')
    if len(groups) > 50: raise PanelAPIError('软件依赖超出限制')
    output = []
    for group in groups:
        if not isinstance(group, list) or not 1 <= len(group) <= 8:
            raise PanelAPIError('软件依赖组格式无效')
        output.append([r.token(v) for v in group])
    return output


def requirements(client, data, params):
    versions = [v for v in data['versions'] if isinstance(v, dict) and
                v.get('m_version') == params['m_version'] and v.get('version') == params['min_version']]
    if len(versions) != 1: raise PanelAPIError('软件版本关联不唯一')
    selected, blockers = versions[0], []
    limits = {}
    for source, key in (('cpu_limit', 'cpu_cores'), ('mem_limit', 'memory_mb'), ('os_limit', 'os_family')):
        value = selected.get(source)
        if type(value) is not int or not 0 <= value <= 10**9:
            raise PanelAPIError('软件资源要求未能识别')
        limits[key] = value
    info = r.system_info(client)
    for key, blocker in (('cpu_cores', 'cpu'), ('memory_mb', 'memory')):
        if limits[key] and (info[key] is None or info[key] < limits[key]): blockers.append(blocker)
    os_family = limits['os_family']
    system = info['system'].lower()
    if (os_family not in (0, 1, 2) or
            os_family == 1 and not re.search(r'centos|red hat|rocky|alma|cloudlinux|anolis', system) or
            os_family == 2 and not re.search(r'ubuntu|debian', system)):
        blockers.append('os')
    groups = dependency_groups(data.get('dependnet')) + dependency_groups(selected.get('dependnet'))
    missing = []
    # The enriched top-level dependency dict already contains only unmet groups.
    top_missing = isinstance(data.get('dependnet'), dict)
    top_count = len(dependency_groups(data.get('dependnet')))
    for number, group in enumerate(groups):
        installed = False
        if not (top_missing and number < top_count):
            for name in group:
                try:
                    if r.software_find(client, name).get('setup') is True:
                        installed = True
                        break
                except (PanelAPIError, KeyError):
                    continue
        if not installed: missing.append(group)
    if missing: blockers.append('dependencies')
    mutex = data.get('mutex', '')
    if mutex == -1: mutex = ''
    mutex_groups = dependency_groups(mutex)
    conflicts = []
    for group in mutex_groups:
        for name in group:
            try:
                if r.software_find(client, name).get('setup') is True: conflicts.append(name)
            except (PanelAPIError, KeyError):
                # An absent optional mutex target is harmless; malformed names were rejected above.
                continue
    if conflicts: blockers.append('mutex')
    # Paid/subscription entitlement has incompatible cloud shapes; fail closed pending a dedicated adapter.
    # This preserves every server restriction and never uses force or an id to bypass checks.
    free = type(data.get('price')) in (int, float) and data['price'] == 0 and data.get('type') not in (8, 12, '8', '12')
    if not free: blockers.append('license')
    return {**limits, 'missing_dependencies': missing, 'mutex_conflicts': sorted(set(conflicts)),
            'license': 'free_catalog' if free else 'requires_verified_entitlement'}, blockers


def uninstall_impact(client, name):
    sites = r.sites(client)
    refs = []
    if name.startswith('php-'):
        branch = r.php_branch(name[4:])
        if not branch: raise PanelAPIError('无法识别要卸载的 PHP 分支')
        refs = [s for s in sites if s['php_version'] == branch]
    elif name in ('nginx', 'apache', 'openlitespeed') and r.webserver(client) == name:
        refs = sites
    count = 0
    if name == 'mysql':
        count = sum(str(v.get('db_type', 0)) == '0' and v.get('type', 'MySQL') == 'MySQL'
                    for v in r.table(client, 'databases'))
    return {'website_refs': [{'id': s['id'], 'name': s['name']} for s in refs], 'local_databases': count}


class InstallSoftware(Operation):
    code, category, label = 'software_install', 'software_install', '安装软件'
    resources = ('software',)

    def validate(self, params):
        strict(params, ('name', 'm_version', 'min_version', 'install_type'), ('name', 'm_version', 'min_version'))
        return {'name': r.token(params['name']), 'm_version': r.token(params['m_version']),
                'min_version': r.token(params['min_version']), 'install_type': install_type(params.get('install_type', '0'))}

    @staticmethod
    def expected(params):
        return params['m_version'] + '.' + params['min_version']

    def check(self, client, params):
        data = r.software_find(client, params['name'])
        snap = software_snapshot(data)
        if not any(v['m_version'] == params['m_version'] and v['min_version'] == params['min_version'] for v in snap['versions']):
            raise PanelAPIError('目标版本不在当前软件目录中')
        if snap['installed'] and snap['installed_version'] != self.expected(params):
            raise PanelAPIError('已安装不同版本，禁止隐含升级或覆盖安装')
        return snap

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        item = child('software:' + params['name'], params['name'], {'name': params['name']}, info, plan=params)
        try:
            snap = self.check(client, params)
            reqs, blockers = requirements(client, r.software_find(client, params['name']), params)
            item['plan'].update(requirements=reqs, blockers=blockers)
            item['before'].update(software=snap, task_ids=queue_snapshot(client, self.task_name(params)))
            if snap['installed']:
                item.update(state='already', message='目标软件版本已安装')
            elif blockers:
                item.update(state='failed', message='资源、依赖、互斥或授权要求不满足，请查看预检明细')
        except (PanelAPIError, ValueError):
            item.update(state='failed', message='软件目录、已安装版本或队列条件不符合要求')
        return preview([item])

    @staticmethod
    def task_name(params):
        return 'php' if params['name'].startswith('php-') else params['name']

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        self.guard(client, item)
        plan = item['plan']
        snap = self.check(client, plan)
        if snap != item['before']['software']:
            raise PanelAPIError('软件目录或实际安装状态已变化')
        reqs, blockers = requirements(client, r.software_find(client, plan['name']), plan)
        if blockers or reqs != plan['requirements']:
            raise PanelAPIError('软件资源、依赖或授权条件已变化')
        queue_snapshot(client, self.task_name(plan))
        client.request('/plugin', 'install_plugin', {'sName': plan['name'], 'version': plan['m_version'],
                       'min_version': plan['min_version'], 'type': plan['install_type']}, mutating=True)
        return self.reconcile(client, item, {'phase': 'apply'})

    def _reconcile(self, client, item, remote):
        plan = item['plan']
        # Queue success is insufficient; read actual installed version independently.
        snap = r.software_option(r.software_find(client, plan['name']))
        if snap['installed'] and snap['installed_version'] == self.expected(plan):
            return result('success', '软件实际安装版本已核实', {'name': plan['name'], 'version': snap['installed_version']})
        remote, state = queue_state(client, item, remote, self.task_name(plan),
                                    {plan['m_version'], self.expected(plan)})
        evidence = {'installed': snap['installed'], 'version': snap['installed_version']}
        if state == 'collision':
            return result('uncertain', '出现无法唯一关联的软件队列任务，请人工核对', evidence, remote)
        if state == 'terminal':
            return result('failed', '安装队列已结束，但实际软件版本不符合目标', evidence, remote)
        return result('uncertain' if remote.get('ambiguous') else 'remote', '等待实际安装版本读回确认', evidence, remote)


class UninstallSoftware(Operation):
    code, category, label = 'software_uninstall', 'software_uninstall', '卸载软件'
    resources = ('software',)

    def validate(self, params):
        strict(params, ('name',), ('name',))
        return {'name': r.token(params['name'])}

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        item = child('software:' + params['name'], params['name'], params, info, plan=params)
        try:
            snap = software_snapshot(r.software_find(client, params['name']))
            item['before'].update(software=snap, task_ids=queue_snapshot(client, InstallSoftware.task_name(params)))
            impact = uninstall_impact(client, params['name'])
            item['plan']['impact'] = impact
            version = snap['installed_version']
            major = [v['m_version'] for v in snap['versions'] if version == v['m_version'] + '.' + v['min_version']]
            item['plan']['version'] = (params['name'][4:] if params['name'].startswith('php-') else
                                       major[0] if len(major) == 1 else version)
            if not snap['installed']:
                item.update(state='already', message='软件尚未安装')
            elif impact['website_refs'] or impact['local_databases']:
                item.update(state='failed', message='仍有网站或本地数据库依赖此软件，禁止卸载')
        except (PanelAPIError, ValueError):
            item.update(state='failed', message='无法核实软件目录或当前安装状态')
        return preview([item])

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        self.guard(client, item)
        name = item['plan']['name']
        if software_snapshot(r.software_find(client, name)) != item['before']['software']:
            raise PanelAPIError('软件目录或已安装版本发生变化')
        impact = uninstall_impact(client, name)
        if impact != item['plan']['impact'] or impact['website_refs'] or impact['local_databases']:
            raise PanelAPIError('软件依赖影响发生变化或仍被使用')
        queue_snapshot(client, InstallSoftware.task_name(item['plan']))
        # Preserve all server-side dependency/database/license safety checks; no id/force bypass.
        client.request('/plugin', 'uninstall_plugin', {'sName': name, 'version': item['plan']['version']}, mutating=True)
        return self.reconcile(client, item, {'phase': 'apply'})

    def _reconcile(self, client, item, remote):
        snap = r.software_option(r.software_find(client, item['plan']['name']))
        if not snap['installed'] and not snap['installed_version']:
            return result('success', '软件卸载状态已读回确认', {'installed': False})
        return result('uncertain' if remote.get('ambiguous') else 'remote', '软件仍存在，等待只读核验',
                      {'installed': snap['installed'], 'version': snap['installed_version']}, remote)


def loaded_modules(client, branch):
    data = client.request('/ajax', 'php_info', {'php_version': branch})
    modules = data.get('modules') if isinstance(data, dict) else None
    if not isinstance(modules, list):
        info = data.get('phpinfo', {}) if isinstance(data, dict) else {}
        modules = info.get('modules', '').split() if isinstance(info, dict) and isinstance(info.get('modules'), str) else None
    if not isinstance(modules, list) or len(modules) > 500 or any(not isinstance(v, str) or len(v) > 100 for v in modules):
        raise PanelAPIError('PHP 模块实际加载状态无法读取')
    return {v.lower().replace(' ', '_') for v in modules}


def extension_snapshot(client, branch, name):
    libs = r.extension_config(client, branch)
    matches = [v for v in libs if isinstance(v, dict) and v.get('name') == name]
    if len(matches) != 1: raise PanelAPIError('扩展不在该 PHP 分支目录中')
    lib = matches[0]
    supported = lib.get('phpversions')
    if isinstance(supported, str): supported = supported.split(',')
    if not isinstance(supported, list) or branch not in [str(v).replace('.', '') for v in supported]:
        raise PanelAPIError('扩展不支持目标 PHP 分支')
    check = lib.get('check')
    if not isinstance(check, str) or not re.fullmatch(r'[A-Za-z0-9_. -]{1,100}', check):
        raise PanelAPIError('扩展模块名称无法验证')
    module = check[:-3] if check.endswith('.so') else name
    aliases = {module.lower().replace(' ', '_'), name.lower()}
    if name.lower() == 'ioncube': aliases.add('ioncube_loader')
    if name.lower() == 'opcache': aliases.add('zend_opcache')
    return {'configured': lib.get('status') is True, 'loaded': bool(loaded_modules(client, branch) & aliases),
            'catalog_digest': digest(json.dumps({k: lib.get(k) for k in ('name', 'check', 'phpversions')}, sort_keys=True))}


class InstallPHPExtension(Operation):
    code, category, label = 'php_extension_install', 'php_extension_install', '安装 PHP 扩展'
    resources = ('php_versions', 'php_extensions', 'webserver')

    def validate(self, params):
        strict(params, ('php_version', 'extensions', 'install_type'), ('php_version', 'extensions'))
        branch = params['php_version']
        if not isinstance(branch, str) or not re.fullmatch(r'[5-9][0-9]', branch):
            raise ValueError('PHP 分支格式无效')
        extensions = params['extensions']
        if not isinstance(extensions, list) or not 1 <= len(extensions) <= MAX_CHILDREN:
            raise ValueError('扩展数量无效')
        names = [r.token(v) for v in extensions]
        if len(set(names)) != len(names): raise ValueError('扩展不能重复')
        return {'php_version': branch, 'extensions': names, 'install_type': install_type(params.get('install_type', '0'))}

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        server, items = r.webserver(client), []
        branch = params['php_version']
        installed = branch in [v['version'] for v in r.php_versions(client)]
        for name in params['extensions']:
            item = child('php:' + branch + ':' + name, 'PHP ' + branch + ' / ' + name,
                         {'php_version': branch, 'name': name}, info,
                         plan={'php_version': branch, 'name': name, 'install_type': params['install_type']})
            if server == 'openlitespeed':
                item.update(state='skipped', message='OpenLiteSpeed 扩展尚未完成适配')
            elif not installed:
                item.update(state='failed', message='目标 PHP 分支未安装')
            else:
                try:
                    snap = extension_snapshot(client, branch, name)
                    item['before'].update(extension=snap, webserver=server, task_ids=queue_snapshot(client, name))
                    if snap['loaded'] and snap['configured']:
                        item.update(state='already', message='扩展已经配置且实际加载')
                except (PanelAPIError, ValueError):
                    item.update(state='failed', message='扩展不适用或加载状态无法核实')
            items.append(item)
        return preview(items)

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        self.guard(client, item)
        plan = item['plan']
        if (r.webserver(client) != item['before']['webserver'] or
                extension_snapshot(client, plan['php_version'], plan['name']) != item['before']['extension']):
            raise PanelAPIError('PHP 扩展配置在预检后变化')
        queue_snapshot(client, plan['name'])
        client.request('/files', 'InstallSoft', {'name': plan['name'], 'version': plan['php_version'],
                       'type': plan['install_type']}, mutating=True)
        return self.reconcile(client, item, {'phase': 'apply'})

    def _reconcile(self, client, item, remote):
        plan = item['plan']
        snap = extension_snapshot(client, plan['php_version'], plan['name'])
        evidence = {'configured': snap['configured'], 'loaded': snap['loaded']}
        if snap['configured'] and snap['loaded']:
            return result('success', '扩展配置及目标 PHP 分支实际加载均已确认', evidence)
        remote, state = queue_state(client, item, remote, plan['name'], {plan['php_version']})
        if state == 'terminal':
            return result('failed', '安装队列已结束，但扩展尚未实际加载', evidence, remote)
        return result('uncertain' if state == 'collision' or remote.get('ambiguous') else 'remote',
                      '等待目标 PHP 分支实际加载扩展', evidence, remote)
