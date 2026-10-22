import re

from .. import resources as r
from ..client import PanelAPIError
from .base import Operation, child, preview, result, strict


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){2}', value):
        raise ValueError('面板版本格式无效')
    return tuple(int(v) for v in value.split('.'))


class UpgradePanel(Operation):
    code, category, label = 'panel_upgrade', 'panel_upgrade', '面板升级'
    resources = ('upgrade_versions',)

    def validate(self, params):
        strict(params, ('version',), ('version',))
        version_tuple(params['version'])
        if params['version'] not in r.SUPPORTED_VERSIONS:
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
        return output

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        current, target = info['version'], params['version']
        item = child('panel-upgrade', '面板 ' + current + ' → ' + target, {'version': target}, info, plan=params)
        if current == target:
            item.update(state='already', message='面板版本已符合目标')
        elif version_tuple(target) < version_tuple(current) or target not in self.offered(client):
            item.update(state='failed', message='目标不是当前官方提供的升级版本，或属于降级')
        return preview([item])

    def _dispatch(self, client, item, phase, remote):
        self.phase(phase)
        self.guard(client, item)
        target = item['plan']['version']
        if target not in self.offered(client):
            raise PanelAPIError('官方候选版本在预检后变化')
        client.request('/system', 'upgrade_panel', {'force': 'true', 'version': target}, mutating=True)
        return result('remote', '面板升级请求已提交，等待重新连接并读取版本', remote={'phase': 'apply', 'version': target})

    def _reconcile(self, client, item, remote):
        info = r.system_info(client)
        if info['version'] == item['plan']['version']:
            return result('success', '重新连接后已核实目标面板版本', {'version': info['version']})
        return result('uncertain' if remote.get('ambiguous') else 'remote',
                      '面板版本尚未达到目标，不会自动重复升级', {'version': info['version']}, remote)
