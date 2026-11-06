"""Official same-branch PHP update; the panel cannot pin the downloaded patch.

Only the existing branch is sent to the official update script. The catalog patch
is a frozen expectation, never proof of what the script installed. A fresh PHP
runtime readback is required for success; a differing result remains uncertain.
"""
import re
import posixpath

from .. import resources as r
from ..client import PanelAPIError
from .base import Operation, child, identity, preview, result, strict
from .software import requirements, software_snapshot, queue_snapshot, tasks, matching


HEALTH_POLICY = 'php_cli_modules_v1'


def cli_modules(value):
    """Bounded names only; never retain phpinfo/ini or equate CLI with FPM.

    Official php_info.php joins get_loaded_extensions() with spaces. Preserve
    known multiword names before splitting that representation. Lists are also
    accepted for adapters returning separate names; unknown/empty is not [].
    """
    if isinstance(value, str):
        if not 1 <= len(value) <= 50000 or re.search(r'[^\x20-\x7e]', value):
            return None
        for name in ('Zend OPcache', 'ionCube Loader', 'Zend Guard Loader'):
            value = re.sub(r'(?<!\S)' + re.escape(name) + r'(?!\S)', name.replace(' ', '_'), value, flags=re.I)
        value = value.split()
    if not isinstance(value, list) or not 1 <= len(value) <= 500:
        return None
    if any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.+ -]{0,99}', name)
           or name != name.strip() for name in value):
        return None
    names = [name.lower().replace(' ', '_') for name in value]
    # Both built-ins exist throughout the supported PHP branches. A partial
    # payload containing only an optional extension cannot be a complete list.
    return sorted(names) if len(set(names)) == len(names) and {'core', 'standard'} <= set(names) else None


def module_check(before, after):
    baseline = before.get('cli_modules')
    observed = after.get('cli_modules')
    baseline_ok = isinstance(baseline, list) and cli_modules(baseline) == baseline
    observed_ok = isinstance(observed, list) and cli_modules(observed) == observed
    if not baseline_ok or not observed_ok:
        return {'state': 'unavailable', 'reason': 'baseline_unavailable' if not baseline_ok else 'readback_unavailable'}
    added, removed = sorted(set(observed) - set(baseline)), sorted(set(baseline) - set(observed))
    return {'state': 'changed' if added or removed else 'unchanged',
            'before_count': len(baseline), 'after_count': len(observed), 'added': added, 'removed': removed}


def numeric_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[5-9]\.[0-9]\.(?:0|[1-9][0-9]{0,2})', value):
        raise ValueError('PHP 实际版本格式无法核实')
    return tuple(int(v) for v in value.split('.'))


def branch_major(branch):
    if not isinstance(branch, str) or not re.fullmatch(r'[5-9][0-9]', branch):
        raise ValueError('PHP 分支格式无效')
    return branch[0] + '.' + branch[1]


def latest_patch(data, major):
    versions = data.get('versions')
    if not isinstance(versions, list) or not 1 <= len(versions) <= 100:
        raise PanelAPIError('PHP 软件目录版本数量无效')
    candidates = []
    for row in versions:
        if not isinstance(row, dict) or row.get('m_version') != major:
            raise PanelAPIError('PHP 软件目录包含其他分支或无效版本')
        value = row.get('version')
        if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]{0,2}', value):
            raise PanelAPIError('PHP 补丁目录格式无效')
        candidates.append(value)
    if len(candidates) != len(set(candidates)):
        raise PanelAPIError('PHP 软件目录版本关联不唯一')
    return max(candidates, key=int)


def runtime_snapshot(client, branch):
    """php_info invokes this branch's PHP and returns PHP_VERSION, without cache."""
    major = branch_major(branch)
    if r.webserver(client) not in ('nginx', 'apache'):
        raise PanelAPIError('OpenLiteSpeed PHP 更新尚未完成适配')
    root = '/www/server/php/' + branch
    # Validate the fixed executable's ancestors as well as its leaf. Do not accept
    # symlinked/custom installations as the official script writes this exact root.
    paths = ('/www', '/www/server', '/www/server/php', root, root + '/bin', root + '/bin/php')
    identities = []
    for path in paths:
        data = client.request('/files', 'get_file_attribute', {'filename': path})
        if (not isinstance(data, dict) or data.get('is_dir') is not (path != paths[-1])
                or data.get('is_link') is not False or data.get('name') != posixpath.basename(path)
                or data.get('path') != posixpath.dirname(path)):
            raise PanelAPIError('PHP 安装目录或可执行文件不存在、为链接或发生漂移')
        device, inode = data.get('st_dev'), data.get('st_ino')
        if (type(device) is not int or not 0 <= device < 2**64
                or type(inode) is not int or not 0 < inode < 2**64):
            raise PanelAPIError('PHP 安装路径 inode 身份无法核实')
        identities.append({'path': path, 'device': device, 'inode': inode})
    response = client.request('/ajax', 'php_info', {'php_version': branch})
    info = response.get('phpinfo') if isinstance(response, dict) else None
    if (not isinstance(info, dict) or info.get('php_path') != '/www/server/php/'
            or info.get('php_bin') != root + '/bin/php' or info.get('php_ini') != root + '/etc/php.ini'):
        raise PanelAPIError('PHP 运行时路径身份无法核实')
    version = info.get('php_version')
    numeric_version(version)
    if not version.startswith(major + '.'):
        raise PanelAPIError('PHP 运行时版本与所选分支不一致')
    return {'version': version, 'php_path': root, 'php_bin': info['php_bin'], 'php_ini': info['php_ini'],
            'path_identities': identities, 'cli_modules': cli_modules(info.get('modules'))}


def update_impact(client, branch):
    sites = r.sites(client)
    if any(site['project_type'] == 'PHP' and not site['php_version'] for site in sites):
        raise PanelAPIError('部分 PHP 网站运行分支无法核实，不能完整确认更新影响')
    refs = [identity(site) for site in sites if site['php_version'] == branch]
    return {'website_refs': sorted(refs, key=lambda site: site['id']),
            'php_service_may_restart': True, 'patch_pinned': False,
            'update_policy': 'official_same_branch', 'runtime_scope': 'branch_cli_binary'}


def update_options(client):
    """Bounded resources for the PHP updater; stale catalog.version is never used."""
    r.system_info(client)
    if r.webserver(client) not in ('nginx', 'apache'):
        return {'php_updates': []}
    options = []
    for entry in r.software_catalog(client):
        match = re.fullmatch(r'php-([5-9])\.([0-9])', entry['name'])
        if not match or not entry['installed']:
            continue
        branch = ''.join(match.groups())
        major = branch_major(branch)
        try:
            data = r.software_find(client, entry['name'])
            if data.get('setup') is not True or type(data.get('type')) is not int or data['type'] != 5:
                continue
            patch = latest_patch(data, major)
            current = runtime_snapshot(client, branch)['version']
            target = major + '.' + patch
            if numeric_version(target) <= numeric_version(current):
                continue
            options.append({'php_version': branch, 'm_version': major, 'min_version': patch,
                            'current_version': current, 'target_version': target, 'install_types': ['0']})
        except (PanelAPIError, ValueError):
            continue
    return {'php_updates': options}


def update_queue_state(client, item, remote):
    """Check the whole target queue before applying any saved task association.

An already associated id must not hide a later human/parallel update. Use one
queue read, and preserve detected conflicts even if a queue row later vanishes.
"""
    plan = item['plan']
    versions = {plan['m_version'], plan['target_version']}
    values = [value for value in tasks(client) if matching(value, 'php', versions)]
    old = set(item['before']['task_ids'])
    candidates = [value for value in values if value['id'] not in old]
    candidate_ids = {value['id'] for value in candidates}
    known = set(remote.get('task_ids', []))
    conflict = (remote.get('collision') or len(candidates) > 1
                or any(value['id'] in old and value['status'] != '1' for value in values)
                or bool(known and known != candidate_ids))
    if conflict:
        return {**remote, 'phase': 'apply', 'task_ids': sorted(known), 'collision': True}, 'collision'
    output = {**remote, 'phase': 'apply', 'task_ids': sorted(candidate_ids)}
    return output, ('terminal' if len(candidates) == 1 and candidates[0]['status'] == '1' else 'pending')


class UpdatePHP(Operation):
    code, category, label = 'php_update', 'php_update', '更新 PHP（同分支）'
    resources = ('php_updates',)
    readback_failure_state = 'uncertain'

    def validate(self, params):
        strict(params, ('php_version', 'm_version', 'min_version', 'install_type'),
               ('php_version', 'm_version', 'min_version'))
        major = branch_major(params['php_version'])
        if params['m_version'] != major:
            raise ValueError('仅允许更新所选已安装 PHP 分支')
        minor = params['min_version']
        if not isinstance(minor, str):
            raise ValueError('PHP 补丁版本格式无效')
        numeric_version(major + '.' + minor)
        # Official fast-install scripts currently map to different patches. Keep
        # this first adapter on the reviewed compile-update path (0 -> 3 on apt).
        if params.get('install_type', '0') != '0':
            raise ValueError('PHP 更新当前仅支持官方编译更新')
        return {'php_version': params['php_version'], 'm_version': major,
                'min_version': minor, 'install_type': '0'}

    @staticmethod
    def name(params):
        return 'php-' + params['m_version']

    def check(self, client, params):
        data = r.software_find(client, self.name(params))
        if data.get('setup') is not True or type(data.get('type')) is not int or data['type'] != 5:
            raise PanelAPIError('目标 PHP 分支未安装或不是官方异步核心软件')
        if latest_patch(data, params['m_version']) != params['min_version']:
            raise PanelAPIError('目标必须是该分支当前目录的最新补丁预期')
        runtime = runtime_snapshot(client, params['php_version'])
        target = params['m_version'] + '.' + params['min_version']
        if numeric_version(runtime['version']) > numeric_version(target):
            raise PanelAPIError('禁止 PHP 补丁降级')
        return data, software_snapshot(data), runtime

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        branch = params['php_version']
        item = child('php-update:' + branch, 'PHP ' + params['m_version'] + ' 同分支更新',
                     {'php_version': branch}, info,
                     plan={**params, 'required_acknowledgements': ['php_update_restart'], 'health_policy': HEALTH_POLICY})
        try:
            data, catalog, runtime = self.check(client, params)
            reqs, blockers = requirements(client, data, params)
            item['before'].update(software=catalog, runtime=runtime, task_ids=queue_snapshot(client, 'php'))
            item['plan'].update(current_version=runtime['version'],
                                target_version=params['m_version'] + '.' + params['min_version'],
                                requirements=reqs, blockers=blockers, impact=update_impact(client, branch))
            if runtime['version'] == item['plan']['target_version']:
                item.update(state='already', message='实际 PHP 版本已符合目标，无需更新')
            elif runtime['cli_modules'] is None:
                item.update(state='failed', message='无法核实更新前的 PHP CLI 模块清单，请重新预检')
            elif blockers:
                item.update(state='failed', message='资源、依赖、互斥或授权要求不满足，请查看预检明细')
            else:
                item['message'] = '官方同分支更新可能重启 PHP；补丁由官方在线脚本决定，须读回核实'
        except (PanelAPIError, ValueError):
            item.update(state='failed', message='PHP 目录、实际版本、路径或队列条件不符合要求')
        return preview([item])

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        plan = item['plan']
        if plan.get('required_acknowledgements') != ['php_update_restart']:
            raise PanelAPIError('缺少冻结的 PHP 重启影响确认项，请重新预检')
        if remote.get('ambiguous'):
            return self.reconcile(client, item, remote)
        if plan.get('health_policy') != HEALTH_POLICY:
            raise PanelAPIError('缺少冻结的 PHP CLI 模块核验策略，请重新预检')
        self.guard(client, item)
        data, catalog, runtime = self.check(client, plan)
        if catalog != item['before']['software'] or runtime != item['before']['runtime']:
            raise PanelAPIError('PHP 目录、实际版本或安装路径发生变化')
        reqs, blockers = requirements(client, data, plan)
        if blockers or reqs != plan['requirements'] or update_impact(client, plan['php_version']) != plan['impact']:
            raise PanelAPIError('PHP 更新要求或网站引用发生变化')
        queue_snapshot(client, 'php')
        # Presence of upgrade selects install_async's mtype='update'. No force/id.
        client.request('/plugin', 'install_plugin', {'sName': self.name(plan), 'version': plan['m_version'],
                       'min_version': plan['min_version'], 'type': plan['install_type'], 'upgrade': '1'}, mutating=True)
        return self.reconcile(client, item, {'phase': 'apply'})

    def _reconcile(self, client, item, remote):
        plan = item['plan']
        runtime = runtime_snapshot(client, plan['php_version'])
        version = runtime['version']
        evidence = {'php_version': plan['php_version'], 'version': version,
                    'target_version': plan['target_version'], 'runtime_scope': 'branch_cli_binary',
                    'cli_module_check': module_check(item['before']['runtime'], runtime),
                    'fpm_runtime_verified': False}
        # make install can replace bin/php, but must not replace the frozen
        # branch directory or its ancestors with a different installation tree.
        if runtime['path_identities'][:-1] != item['before']['runtime']['path_identities'][:-1]:
            return result('uncertain', 'PHP 安装目录身份在更新后变化，请人工核对', evidence, remote)
        if version not in (plan['target_version'], item['before']['runtime']['version']):
            return result('uncertain', '实际 PHP 补丁与冻结预期不一致，请人工核对；不会重复更新', evidence, remote)
        remote, state = update_queue_state(client, item, remote)
        if state == 'collision':
            return result('uncertain', '出现无法唯一关联的 PHP 队列任务，请人工核对', evidence, remote)
        if remote.get('ambiguous'):
            return result('uncertain', 'PHP 更新请求曾发生传输歧义，请人工核对；不会重复更新', evidence, remote)
        if state == 'terminal':
            if version == plan['target_version']:
                check = evidence['cli_module_check']
                if plan.get('health_policy') == HEALTH_POLICY:
                    if check['state'] == 'unavailable':
                        return result('uncertain', '实际补丁已读回，但 CLI 模块对照缺少依据，请人工核对；不会重复更新', evidence, remote)
                    if check['removed']:
                        return result('uncertain', '实际补丁已读回，但部分 PHP CLI 模块不再加载，请人工核对；不会重复更新', evidence, remote)
                return result('success', '唯一关联的 PHP 更新队列已结束，实际补丁已读回核实', evidence)
            return result('failed', 'PHP 更新队列已结束，但实际版本仍未达到预期', evidence, remote)
        return result('remote', '等待唯一关联的 PHP 更新队列结束并读回实际版本', evidence, remote)
