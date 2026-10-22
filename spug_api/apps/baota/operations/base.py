"""Durable adapter contract.

validate() rejects unknown keys and produces bounded JSON. preflight() expands selectors
ONCE, on one panel, into independent child items. The worker persists each entire item.
execute() is dispatch(..., 'apply'). Before EVERY dispatch the worker must durably save
the dispatch intent and mark the panel busy. dispatch performs at most ONE mutating API
request; never loop over children or phases. All panel mutations must be serialized.
One API request can have broad server-side effects: SSL apply/renew automatically sync
eligible same-SAN certificates panel-wide, including unselected/orphan copies and panel
TLS, and reload Web services. The initial intent covers that whole frozen policy. The
task service MUST persist each required acknowledgement before initial dispatch; no
adapter input can narrow or suppress Baota's automatic sync. The registered-site
inventory is explicitly incomplete and never claims to cover orphan/panel certificates.

Persist the returned {state,message,evidence,remote}. Reconcile is READ ONLY and may
return remote.next_action='deploy'. Persist that result, then a separate dispatch intent,
then call dispatch(..., 'deploy', remote). 'remote' and 'uncertain' are not success.
An already auto-deployed selected SSL certificate needs no SetCertToSite mutation.
Panel TLS pin changes remain uncertain/locked; never automatically trust a new pin.
Rewrite file readback proves saving only: runtime reload is unverified and remains
uncertain, including API child status:true. Reconcile cannot promote file-only evidence.
After transport ambiguity the worker polls reconcile; it MUST NOT automatically resend
any mutation. An unchanged readback cannot prove a timed-out request was not accepted.
An operator may start a new preflight after resolving uncertainty. Persisted plans are
trusted server snapshots; never reconstruct items/remote directly from browser JSON.

Snapshot hashes detect drift; they are not backups. Never auto-restore configuration.
Only allowlisted primitives are stored; transport JSON, PEM, keys and logs stay local.
The fixed two-version compatibility matrix is based on official-source inspection, not
a claim of testing every action/distro. See docs/research for runtime evidence.
"""
import hashlib

from .. import resources as r
from ..client import PanelAPIError
from ..validation import positive_id


MAX_CHILDREN = 200


def strict(data, allowed, required=()):
    if not isinstance(data, dict) or set(data) - set(allowed) or set(required) - set(data):
        raise ValueError('操作参数缺失或包含未知字段')


def boolean(value):
    if type(value) is not bool:
        raise ValueError('开关必须是布尔值')
    return value


def selection(data):
    if data.get('all_sites') is True and 'sites' not in data:
        return {'all_sites': True}
    ids = data.get('sites')
    if 'all_sites' in data or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_CHILDREN:
        raise ValueError('请选择网站或选择全部网站')
    result = [positive_id(v) for v in ids]
    if len(set(result)) != len(result):
        raise ValueError('网站不能重复')
    return {'sites': result}


def selected_sites(client, params):
    available = r.sites(client)
    if params.get('all_sites') is True:
        if len(available) > MAX_CHILDREN:
            raise PanelAPIError('网站数量超出单次任务限制')
        return available
    by_id = {s['id']: s for s in available}
    return [by_id.get(sid, {'id': sid, 'name': str(sid), 'missing': True}) for sid in params['sites']]


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def identity(site):
    return {k: site.get(k) for k in ('id', 'name', 'path', 'project_type', 'php_version', 'status')}


def result(state, message, evidence=None, remote=None):
    data = {'state': state, 'message': message, 'evidence': evidence or {}}
    if remote is not None:
        data['remote'] = remote
    return data


def preview(items):
    summary = {state: sum(i['state'] == state for i in items) for state in ('ready', 'already', 'skipped', 'failed')}
    return {'items': items, 'summary': {**summary, 'total': len(items)}}


def child(key, label, target, info, plan=None, before=None, state='ready', message='预检通过'):
    return {'key': key, 'label': label, 'target': dict(target), 'before': {
        'panel_version': info['version'], **(before or {})}, 'plan': dict(plan or {}), 'state': state, 'message': message}


def file_body(client, path):
    response = client.request('/files', 'GetFileBody', {'path': path})
    text = response.get('data') if isinstance(response, dict) else None
    if not isinstance(text, str) or len(text.encode('utf-8')) > 128 * 1024:
        raise PanelAPIError('配置文件读取失败或内容超出限制')
    return text.replace('\r\n', '\n')


class Operation:
    category = ''
    code = ''
    readback_failure_state = 'remote'

    def execute(self, client, item):
        return self.dispatch(client, item, 'apply')

    def guard(self, client, item, site=False):
        info = r.system_info(client)
        if info['version'] != item['before']['panel_version']:
            raise PanelAPIError('面板版本在预检后变化，请重新预检')
        if site:
            current = next((s for s in r.sites(client) if s['id'] == item['target']['id']), None)
            if current is None or identity(current) != item['before']['identity']:
                raise PanelAPIError('网站身份或配置在预检后变化，请重新预检')
            return current
        return info

    def dispatch(self, client, item, phase='apply', remote=None):
        if item['state'] != 'ready':
            return result(item['state'], item['message'])
        try:
            return self._dispatch(client, item, phase, remote or {})
        except PanelAPIError as exc:
            # Exception strings are intentionally not propagated: alternate clients may leak bodies.
            if exc.uncertain:
                saved = {**(remote or {}), 'phase': phase, 'ambiguous': True}
                saved.pop('next_action', None)
                return result('uncertain', '请求结果不确定，仅允许只读核验，不会自动重发', remote=saved)
            return result('failed', '面板条件已变化或接口拒绝操作，请重新预检')
        except ValueError:
            return result('failed', '操作参数或远端资源格式无效')

    def reconcile(self, client, item, remote):
        try:
            return self._reconcile(client, item, remote or {})
        except (PanelAPIError, ValueError):
            return result('uncertain' if (remote or {}).get('ambiguous') else self.readback_failure_state,
                          '暂时无法读回结果，请继续只读核验', remote=remote or {})

    @staticmethod
    def phase(phase, expected='apply'):
        if phase != expected:
            raise ValueError('操作阶段无效')
