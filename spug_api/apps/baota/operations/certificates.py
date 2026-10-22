"""HTTP ACME issuance and deployment, separated by a durable worker boundary."""
import datetime
import json
import posixpath
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .. import resources as r
from ..client import PanelAPIError
from .base import Operation, result, selection, strict
from .sites import domain_list, site_preflight


INDEX = re.compile(r'[a-fA-F0-9]{16,128}\Z')


def order_index(value):
    if not isinstance(value, str) or not INDEX.fullmatch(value):
        raise PanelAPIError('证书订单关联标识无效')
    return value.lower()


def certificate_info(pem, require_valid=True):
    """Parse only internally, return public metadata. Never persist a PEM or private key."""
    if not isinstance(pem, str) or len(pem.encode('utf-8')) > 128 * 1024:
        raise PanelAPIError('证书内容无效')
    try:
        cert = x509.load_pem_x509_certificate(pem.encode('ascii'))
        names = sorted(set(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)))
        start = getattr(cert, 'not_valid_before_utc', None)
        end = getattr(cert, 'not_valid_after_utc', None)
        if start is None:
            start = cert.not_valid_before.replace(tzinfo=datetime.timezone.utc)
            end = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        if require_valid and (start > now + datetime.timedelta(minutes=5) or end <= now):
            raise ValueError('certificate dates')
        if len(names) > 100 or any(not isinstance(n, str) or len(n) > 253 for n in names):
            raise ValueError('certificate SAN')
        return {'fingerprint': cert.fingerprint(hashes.SHA256()).hex(), 'domains': names,
                'expires_at': end.isoformat(), 'not_before': start.isoformat()}
    except (ValueError, UnicodeError, x509.ExtensionNotFound, TypeError):
        raise PanelAPIError('证书格式、域名或有效期不符合要求') from None


def ssl_snapshot(client, site):
    data = client.request('/site', 'GetSSL', {'siteName': site['name']}, allow_status_false=True)
    if not isinstance(data, dict) or type(data.get('status')) is not bool:
        raise PanelAPIError('网站 SSL 状态格式无效')
    certificate = {}
    if data.get('csr'):
        certificate = certificate_info(data['csr'], require_valid=False)
    index = data.get('index', '')
    if index: index = order_index(index)
    return {'enabled': data['status'], 'certificate': certificate, 'index': index,
            'auth_type': data.get('auth_type') if data.get('auth_type') in ('http', 'dns', 'tls') else '',
            'type': data.get('type') if type(data.get('type')) is int else -1}


def orders(client):
    response = client.request('/acme', 'get_orders')
    if not isinstance(response, list) or len(response) > r.MAX_RESOURCES:
        raise PanelAPIError('证书订单列表无效')
    values = []
    for raw in response:
        if not isinstance(raw, dict): raise PanelAPIError('证书订单格式无效')
        try:
            index = order_index(raw.get('index'))
        except PanelAPIError:
            continue
        # Only selectors/paths used internally. None of the raw order is stored or returned.
        values.append({'index': index, 'domains': raw.get('domains'), 'auth_type': raw.get('auth_type'),
                       'auth_to': raw.get('auth_to'), 'status': raw.get('status'),
                       'save_path': raw.get('save_path'), 'ca': raw.get('ca')})
    if len({v['index'] for v in values}) != len(values):
        raise PanelAPIError('证书订单关联不唯一')
    return values


def run_path(client, site):
    data = client.request('/site', 'GetSiteRunPath', {'id': site['id']})
    run = data.get('runPath') if isinstance(data, dict) else None
    if (not isinstance(run, str) or not run.startswith('/') or '..' in run.split('/')
            or re.search(r'[\x00-\x20\x7f\\;$`|&<>]', run) or len(run) > 512):
        raise PanelAPIError('网站运行目录无法核实')
    root = site['path'].rstrip('/')
    path = posixpath.normpath(root + '/' + run.lstrip('/'))
    if path != root and not path.startswith(root + '/'):
        raise PanelAPIError('证书验证目录不属于网站')
    return path


def matches(order, plan):
    try:
        names = domain_list(order.get('domains'))
    except ValueError:
        return False
    return (set(names) == set(plan['domains']) and order['auth_type'] == 'http'
            and order['auth_to'] == plan['auth_to'] and order['ca'] in (None, False, '', 'letsencrypt'))


def issued_certificate(client, order, plan, allow_expired=False):
    if order.get('status') != 'valid':
        return None
    names = domain_list(order['domains'])
    expected = 'vhost/letsencrypt/' + names[0]
    if order.get('save_path') not in (expected, '/www/server/panel/' + expected):
        raise PanelAPIError('已签发证书不在该订单允许的保存目录内')
    response = client.request('/files', 'GetFileBody', {'path': '/www/server/panel/' + expected + '/fullchain.pem'})
    pem = response.get('data') if isinstance(response, dict) else None
    info = certificate_info(pem, require_valid=not allow_expired)
    if not set(plan['domains']).issubset(info['domains']):
        raise PanelAPIError('已签发证书未覆盖选定域名')
    return info


class CertificateOperation(Operation):
    category = 'ssl'
    resources = ('sites', 'domains', 'ssl')
    renewal = False

    def validate(self, params):
        strict(params, ('sites', 'all_sites', 'domains'))
        clean = selection(params)
        mapping = params.get('domains', {})
        if not isinstance(mapping, dict) or len(mapping) > 200:
            raise ValueError('网站域名映射格式无效')
        mapped = {}
        for key, values in mapping.items():
            if not isinstance(key, str) or not re.fullmatch(r'[1-9][0-9]{0,17}', key):
                raise ValueError('网站域名映射标识无效')
            if 'sites' in clean and int(key) not in clean['sites']:
                raise ValueError('不能指定未选择网站的域名')
            mapped[key] = domain_list(values)
        return {**clean, 'domains': mapped}

    def preflight(self, client, params):
        params, info = self.validate(params), r.system_info(client)
        server = r.webserver(client)
        all_orders = orders(client)
        def prepare(site, item):
            if server == 'openlitespeed':
                item.update(state='skipped', message='OpenLiteSpeed SSL 尚未完成此操作适配')
                return
            if site['status'] != '1':
                item.update(state='skipped', message='网站未启用，不适用 HTTP 文件验证')
                return
            bound = r.domains(client, site['id'])
            available = sorted({v['name'] for v in bound})
            requested = params['domains'].get(str(site['id']), available)
            requested = domain_list(requested)
            if not set(requested).issubset(available):
                raise PanelAPIError('请求域名不属于当前网站')
            auth_to = run_path(client, site)
            snapshot = ssl_snapshot(client, site)
            item['before'].update(ssl=snapshot, domains=bound, webserver=server,
                                  order_ids=[v['index'] for v in all_orders])
            plan = item['plan'] = {'domains': requested, 'auth_to': auth_to, 'renewal': self.renewal}
            if self.renewal:
                index = order_index(snapshot['index'])
                linked = [v for v in all_orders if v['index'] == index and matches(v, plan)]
                if (not snapshot['enabled'] or not snapshot['certificate'] or snapshot['auth_type'] != 'http'
                        or snapshot['type'] != 1 or len(linked) != 1):
                    raise PanelAPIError("没有唯一关联的 HTTP Let's Encrypt 订单")
                plan['index'] = index
            current = snapshot['certificate']
            if snapshot['enabled'] and current and set(requested).issubset(current['domains']):
                expires = datetime.datetime.fromisoformat(current['expires_at'])
                if expires > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30):
                    item.update(state='already', message='网站已部署覆盖目标域名且有效期超过 30 天的证书')
                    return
            if not self.renewal:
                matching_orders = [v for v in all_orders if matches(v, plan)]
                pending = [v for v in matching_orders if v.get('status') != 'valid']
                if pending: raise PanelAPIError('该网站已有相同域名的未完成订单')
                if len(matching_orders) == 1:
                    cert = issued_certificate(client, matching_orders[0], plan, allow_expired=True)
                    if cert and (cert['fingerprint'] != current.get('fingerprint') and
                            datetime.datetime.fromisoformat(cert['expires_at']) >
                            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)):
                        plan['reuse_index'] = matching_orders[0]['index']
        return site_preflight(client, params, info, prepare)

    def guard_site(self, client, item):
        site = self.guard(client, item, site=True)
        if (r.webserver(client) != item['before']['webserver']
                or r.domains(client, site['id']) != item['before']['domains']
                or run_path(client, site) != item['plan']['auth_to']):
            raise PanelAPIError('SSL 网站身份、域名或验证路径已变化')
        return site

    def _dispatch(self, client, item, phase, remote):
        if phase not in ('apply', 'deploy'):
            raise ValueError('证书操作阶段无效')
        site = self.guard_site(client, item)
        current = ssl_snapshot(client, site)
        plan = item['plan']
        if phase == 'apply':
            if current != item['before']['ssl']:
                raise PanelAPIError('网站 SSL 配置在预检后变化')
            latest = orders(client)
            if {v['index'] for v in latest if matches(v, plan)} != {
                    v for v in item['before']['order_ids'] if any(o['index'] == v and matches(o, plan) for o in latest)}:
                raise PanelAPIError('证书订单在预检后出现冲突')
            if plan.get('reuse_index'):
                return self.reconcile(client, item, {'phase': 'issue', 'index': plan['reuse_index']})
            if self.renewal:
                linked = [o for o in latest if o['index'] == plan['index'] and matches(o, plan)]
                if len(linked) != 1: raise PanelAPIError('续签订单关联已变化')
                response = client.request('/acme', 'renew_cert', {'index': plan['index']}, mutating=True)
                remote = {'phase': 'issue', 'index': plan['index']}
            else:
                response = client.request('/acme', 'apply_cert_api', {'id': site['id'],
                    'domains': json.dumps(plan['domains']), 'auth_type': 'http', 'auth_to': plan['auth_to']}, mutating=True)
                remote = {'phase': 'issue'}
            # A return body may contain private_key/cert/raw logs. Select only a valid index.
            if isinstance(response, dict) and isinstance(response.get('index'), str) and INDEX.fullmatch(response['index']):
                remote['index'] = response['index'].lower()
            return self.reconcile(client, item, remote)
        if remote.get('next_action') != 'deploy' or not remote.get('certificate'):
            raise ValueError('必须先持久化已签发证书核验结果')
        index = order_index(remote.get('index'))
        selected = [v for v in orders(client) if v['index'] == index and matches(v, plan)]
        if len(selected) != 1: raise PanelAPIError('证书部署订单不再属于目标网站')
        cert = issued_certificate(client, selected[0], plan)
        if not cert or cert != remote['certificate']:
            raise PanelAPIError('签发证书在部署前发生变化')
        if current != item['before']['ssl']:
            if current['enabled'] and current['certificate'].get('fingerprint') == cert['fingerprint']:
                return result('success', '目标证书已部署并读回确认', cert)
            raise PanelAPIError('网站 SSL 配置在部署前发生变化')
        client.request('/acme', 'SetCertToSite', {'index': index, 'siteName': site['name']}, mutating=True)
        return self.reconcile(client, item, {'phase': 'deploy', 'index': index, 'certificate': cert})

    def _reconcile(self, client, item, remote):
        site = self.guard_site(client, item)
        plan = item['plan']
        values = orders(client)
        index = remote.get('index')
        if index:
            index = order_index(index)
            candidates = [v for v in values if v['index'] == index and matches(v, plan)]
        else:
            old = set(item['before']['order_ids'])
            candidates = [v for v in values if v['index'] not in old and matches(v, plan)]
            # Renew reuses the linked index in the official adapter.
            if self.renewal:
                candidates = [v for v in values if v['index'] == plan['index'] and matches(v, plan)]
        if len(candidates) > 1:
            return result('uncertain', '新证书订单无法唯一关联，请人工核对', remote={**remote, 'collision': True})
        if not candidates:
            return result('uncertain' if remote.get('ambiguous') else 'remote',
                          '尚未读到属于目标网站的证书订单，不会自动重发', remote=remote)
        order = candidates[0]
        remote = {**remote, 'index': order['index'], 'phase': remote.get('phase', 'issue')}
        cert = issued_certificate(client, order, plan)
        if not cert:
            return result('remote', '证书订单仍在签发处理中，尚未签发或部署', remote=remote)
        previous = item['before']['ssl']['certificate']
        if (not self.renewal and previous and cert['fingerprint'] == previous.get('fingerprint')):
            return result('remote', '仍读取到申请前的旧证书，等待新证书', remote=remote)
        if self.renewal and (cert['fingerprint'] == previous.get('fingerprint') or
                cert['expires_at'] <= previous.get('expires_at', '')):
            return result('remote', '仍读取到续签前的旧证书，等待新证书', remote=remote)
        current = ssl_snapshot(client, site)
        if current['enabled'] and current['certificate'].get('fingerprint') == cert['fingerprint']:
            return result('success', '证书域名、有效期及网站部署指纹已读回确认', cert)
        if remote.get('phase') == 'deploy':
            return result('uncertain' if remote.get('ambiguous') else 'remote',
                          '证书已签发，但网站尚未读回相同证书；不会自动再次部署',
                          {'certificate': cert, 'deployed': False}, {**remote, 'certificate': cert})
        return result('remote', '证书已签发，等待独立持久化部署阶段', {'certificate': cert},
                      {**remote, 'phase': 'issue', 'next_action': 'deploy', 'certificate': cert})


class ApplyCertificate(CertificateOperation):
    code, label = 'ssl_apply', 'SSL 申请及部署'


class RenewCertificate(CertificateOperation):
    code, label, renewal = 'ssl_renew', 'SSL 续签及部署', True
