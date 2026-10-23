import re

from .. import resources as r
from ..client import PanelAPIError
from ..validation import is_linux_system
from .base import Operation, child, preview, result, strict


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){2}', value):
        raise ValueError('面板版本格式无效')
    return tuple(int(v) for v in value.split('.'))


class UpgradePanel(Operation):
    code, category, label = 'panel_upgrade', 'panel_upgrade', '面板升级'
    resources = ('upgrade_versions',)
    LEGACY_POLICY = 'baota_legacy_stable_v1'
    LEGACY_NOTICE = '官方稳定通道在后台决定实际版本，预检候选无法冻结；升级期间面板会暂时断开连接'

    def validate(self, params):
        if isinstance(params, dict) and 'channel' in params:
            strict(params, ('channel',), ('channel',))
            if params['channel'] != 'stable':
                raise ValueError('旧版面板仅支持官方稳定通道')
            return {'channel': 'stable'}
        strict(params, ('version',), ('version',))
        version_tuple(params['version'])
        if params['version'] not in r.EXACT_UPGRADE_TARGETS:
            raise ValueError('目标版本尚未完成接口适配')
        return {'version': params['version']}

    @staticmethod
    def offered(client):
        # Presence of force (even false!) executes an upgrade in official implementations.
        data = client.request('/system', 'upgrade_panel', {})
        if not isinstance(data, dict) or not isinstance(data.get('cloud'), dict):
            raise PanelAPIError('无法读取官方升级候选版本')
        output = set()
        for key in ('OfficialVersion', 'OfficialVersionLatest'):
            value = data['cloud'].get(key)
            if isinstance(value, dict):
                version = value.get('version')
                version_tuple(version)
                output.add(version)
        if not output:
            raise PanelAPIError('官方升级候选字段缺失或无效')
        return output

    @staticmethod
    def legacy_candidate(client):
        # 9.2's default official channel is distinct from ?v=lts and beta enrollment.
        # Never use ajax/UpdatePanel: even a check may execute an automatic upgrade.
        data = client.request('/system', 'upgrade_panel', {})
        cloud = data.get('cloud') if isinstance(data, dict) else None
        if not isinstance(cloud, dict):
            raise PanelAPIError('无法读取官方稳定通道候选版本')
        version = cloud.get('version')
        version_tuple(version)
        package_hash, update_time = cloud.get('hash'), cloud.get('update_time')
        if not isinstance(package_hash, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', package_hash):
            raise PanelAPIError('官方候选包摘要无效')
        if isinstance(update_time, bool) or not re.fullmatch(r'[1-9][0-9]{0,11}', str(update_time)):
            raise PanelAPIError('官方候选版本时间无效')
        return {'checked_version': version, 'update_time': int(update_time)}

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        if 'channel' in params:
            if info['version'] != '9.2.0':
                return preview([child('panel-upgrade', '面板官方稳定通道升级', params, info,
                    state='failed', message='该版本请使用已适配的精确目标版本升级')])
            candidate = self.legacy_candidate(client)
            plan = {'mode': 'legacy_stable', 'channel': 'stable', **candidate, 'policy': self.LEGACY_POLICY,
                    'required_acknowledgements': ['legacy_panel_upgrade']}
            item = child('panel-upgrade', '面板 ' + info['version'] + ' → 官方稳定通道（检查候选 ' +
                         candidate['checked_version'] + '）', params, info, plan=plan,
                         before={**candidate, 'policy': self.LEGACY_POLICY}, message=self.LEGACY_NOTICE)
            if candidate['checked_version'] == info['version']:
                item.update(state='already', message='当前版本与官方稳定通道检查候选相同，无需升级')
            elif version_tuple(candidate['checked_version']) < version_tuple(info['version']):
                item.update(state='failed', message='官方稳定通道检查候选低于当前版本，禁止降级')
            return preview([item])
        current, target = info['version'], params['version']
        item = child('panel-upgrade', '面板 ' + current + ' → ' + target, {'version': target}, info, plan=params)
        if current == '9.2.0':
            item.update(state='failed', message='9.2.0 无法指定升级版本，请选择官方稳定通道并确认后台决定实际版本')
        elif current == target:
            item.update(state='already', message='面板版本已符合目标')
        elif version_tuple(target) < version_tuple(current) or target not in self.offered(client):
            item.update(state='failed', message='目标不是当前官方提供的升级版本，或属于降级')
        return preview([item])

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        plan, before = item.get('plan'), item.get('before')
        if not isinstance(plan, dict) or not isinstance(before, dict):
            raise ValueError('缺少冻结的面板升级计划')
        if before.get('panel_version') == '9.2.0' or plan.get('mode') == 'legacy_stable':
            fields = ('mode', 'channel', 'checked_version', 'update_time', 'policy', 'required_acknowledgements')
            strict(plan, fields, fields)
            if (before.get('panel_version') != '9.2.0' or plan.get('mode') != 'legacy_stable'
                    or plan.get('channel') != 'stable' or item.get('target') != {'channel': 'stable'}
                    or plan.get('policy') != self.LEGACY_POLICY or before.get('policy') != self.LEGACY_POLICY
                    or plan.get('required_acknowledgements') != ['legacy_panel_upgrade']):
                raise ValueError('旧版升级策略无效')
            expected = {key: plan.get(key) for key in ('checked_version', 'update_time')}
            if (version_tuple(plan['checked_version']) <= version_tuple(before['panel_version'])
                    or type(plan['update_time']) is not int or type(before.get('update_time')) is not int
                    or any(before.get(key) != value for key, value in expected.items())):
                raise ValueError('旧版升级候选快照无效')
            self.guard(client, item)
            if self.legacy_candidate(client) != expected:
                raise PanelAPIError('官方稳定通道候选在预检后变化')
            # The official background process queries its channel again. No version is sent.
            client.request('/system', 'upgrade_panel', {'force': 'true'}, mutating=True)
            return result('remote', '官方稳定通道升级请求已提交，等待重新连接并只读核验；实际版本由官方后台决定',
                          remote={'phase': 'apply', 'channel': 'stable', 'policy': self.LEGACY_POLICY})
        self.guard(client, item)
        target = item['plan']['version']
        if target not in r.EXACT_UPGRADE_TARGETS or target not in self.offered(client):
            raise PanelAPIError('官方候选版本在预检后变化')
        client.request('/system', 'upgrade_panel', {'force': 'true', 'version': target}, mutating=True)
        return result('remote', '面板升级请求已提交，等待重新连接并读取版本', remote={'phase': 'apply', 'version': target})

    def _reconcile(self, client, item, remote):
        # Readback must survive an official upgrade to a not-yet-adapted version.
        # This bounded reader is only for observation; preflight/dispatch still use system_info.
        info = client.request('/system', 'GetSystemTotal')
        if not isinstance(info, dict) or not is_linux_system(info):
            raise PanelAPIError('无法识别升级后面板系统')
        version = info.get('version')
        version_tuple(version)
        evidence = {'version': version, 'version_supported': version in r.SUPPORTED_VERSIONS}
        if item['plan'].get('mode') == 'legacy_stable':
            evidence.update(channel='stable', checked_version=item['plan']['checked_version'],
                            target_version_frozen=False)
            if not evidence['version_supported']:
                return result('uncertain', '已读回面板版本，但该版本尚未适配；请人工核实，不会自动重复升级', evidence, remote)
            if version == item['plan']['checked_version']:
                return result('success', '已读回与预检候选相符的面板版本；实际版本由官方稳定通道后台决定', evidence)
            if version != item['before']['panel_version']:
                return result('uncertain', '已读回面板版本变化，但与预检候选不同，请人工核实；不会自动重复升级', evidence, remote)
            return result('uncertain' if remote.get('ambiguous') else 'remote',
                          '面板版本尚未变化，继续只读核验；不会自动重复升级', evidence, remote)
        if not evidence['version_supported']:
            return result('uncertain', '已读回面板版本，但该版本尚未适配；请人工核实，不会自动重复升级', evidence, remote)
        if version == item['plan']['version']:
            return result('success', '重新连接后已核实目标面板版本', evidence)
        return result('uncertain' if remote.get('ambiguous') else 'remote',
                      '面板版本尚未达到目标，不会自动重复升级', evidence, remote)
