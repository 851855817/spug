"""Database-only public API, opt-in configuration and durable admission."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone as utc_timezone
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.account.models import User
from apps.host.models import Host
from apps.baota.models import Panel
from apps.setting.models import UserSetting
from .models import SSLSource, SSLCertificate, SSLJob, SSLLease, SSLReminder, SSLReminderPreference
from .remote_probe import DISCOVERY_MESSAGES


INTERVALS = (1, 6, 12, 24)
COOLDOWN = 60
LEASE_SECONDS = 600
MAX_SOURCES = 10
MAX_CERTIFICATES = 100
ACTIVE = ('queued', 'running', 'prepared', 'uncertain')
VISIBILITY_KEY = 'ssl_hidden_certificates'
MAX_HIDDEN_CERTIFICATES = 10000
STATES = ('normal', 'expiring', 'expired', 'error', 'unknown', 'unconfigured', 'stale')
MESSAGES = {**DISCOVERY_MESSAGES, 'timeout': '检查超时，请缩小来源范围后重试', 'auth_failed': '来源认证失败，请检查连接配置',
    'host_unverified': 'SSH 主机尚未验证', 'panel_unconfigured': '宝塔 API 尚未配置并验证',
    'unsupported': '来源配置无法完整识别，请查看工具与配置指导',
    'invalid_response': '来源返回的数据无法安全解析', 'output_limit': '来源数据超过单次检查限制，请缩小范围',
    'collection_failed': '检查未完成，请核对来源连接和配置', 'stale_plan': '预检后配置发生变化，请重新预检',
    'unsupported_renewal': '当前证书订单不支持安全自动续签，请查看工具指导',
    'source_disabled': 'SSL 来源已关闭，后续操作已停止', 'lease_expired': '任务租约已过期，请重新检查',
    'python_missing': '远端缺少可用的 Python 3，无法运行固定只读 SSL 探针'}
RENEWAL_REASONS = {'missing_tool': '未安装可用的 acme.sh 或 Certbot，请按官方文档配置后重新检测',
    'missing_order': '未找到映射到当前部署证书的续签订单', 'ambiguous_order': '发现多个匹配订单，无法唯一确定所选订单',
    'unsupported_hook': '订单包含未知 hook 或自定义命令，请人工核对', 'unsupported_config': '订单配置包含动态路径或不支持的配置',
    'unverified_service': '无法确认固定 Web 服务重载方式，请先明确部署配置',
    'domain_mismatch': '订单域名与部署证书不匹配', 'deployment_mismatch': '订单证书尚未部署到当前网站证书路径',
    'unverified_includes': '已核对源站证书，配置引用尚未完整核实，请从既有面板任务预检续签或关联 SSH 检查'}
TLS_MESSAGES = frozenset(('公网 DNS 解析失败', '公网 DNS 结果为空或超出限制', '公网 DNS 结果不受支持',
    '公网检查拒绝私有、回环及特殊网络地址', '公网 TLS 连接或握手失败',
    '公网证书验证失败：证书链、域名或有效期不可信', '域名格式无效', '通配符不能用于公网握手',
    '公网检查仅接受 DNS 域名', 'TLS 端口无效', 'SSL 检查超时', 'SSL 检查失败，请检查连接和配置',
    'SSL 检查数据格式无效', 'SSL 检查输出超限', '通配符仅检查本地部署证书'))
TLS_MESSAGES |= frozenset(('公网证书已过期', '公网证书尚未生效', '公网证书域名不匹配',
                          '公网证书为自签名，未受信任', '公网证书链未受信任'))


class WorkflowError(ValueError):
    def __init__(self, code):
        self.code = code if code in MESSAGES else 'collection_failed'
        super().__init__(MESSAGES[self.code])


def permission(user, lock=False):
    qs = User.objects.select_for_update() if lock else User.objects
    current = qs.filter(pk=getattr(user, 'pk', user if isinstance(user, int) else None),
                        is_active=True, is_deleted=False, is_supper=True).first()
    if current is None:
        raise PermissionError('仅活跃超级管理员可访问 SSL 监控')
    return current


def strict(data, allowed):
    if not hasattr(data, 'keys') or set(data) - set(allowed):
        raise ValueError('请求参数无效')
    if hasattr(data, 'getlist') and any(len(data.getlist(key)) != 1 for key in data):
        raise ValueError('请求参数重复')


def integer(value, maximum=1000000000):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)) or int(value) > maximum:
        raise ValueError('编号或分页范围无效')
    return int(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.get_default_timezone())
    return value.astimezone(utc_timezone.utc).isoformat()


def instant(value):
    if not isinstance(value, str) or len(value) > 40 or 'T' not in value:
        raise ValueError('证书时间必须为完整 UTC 时刻')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(utc_timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError('证书时间格式无效') from None


def utcnow():
    return datetime.now(utc_timezone.utc)


def safe_error(error):
    if isinstance(error, TimeoutError):
        return MESSAGES['timeout']
    return MESSAGES.get(getattr(error, 'code', ''), MESSAGES['collection_failed'])


def source_object(key, lock=False):
    if not isinstance(key, str) or not re.fullmatch(r'(host|panel):[1-9][0-9]{0,15}', key):
        raise ValueError('来源编号无效')
    kind, pk = key.split(':')
    cls = Host if kind == 'host' else Panel
    qs = cls.objects.select_for_update() if lock else cls.objects
    obj = qs.filter(pk=int(pk)).first()
    if obj is None:
        raise ValueError('来源不存在')
    return kind, obj


def config_digest(kind, obj):
    if kind == 'host':
        fields = [obj.hostname, obj.port, obj.username, obj.private_key, obj.is_verified]
    else:
        fields = [obj.api_base_url, obj.api_key_ciphertext, obj.tls_ca, obj.tls_fingerprint,
                  obj.api_config_revision, obj.api_version, obj.api_checked_at]
    return digest(fields)


def server_key(key):
    kind, obj = source_object(key)
    if kind == 'panel':
        from apps.baota.resource_monitor.models import ResourceMonitorPreference
        link = ResourceMonitorPreference.objects.filter(panel_id=obj.pk, linked_host__isnull=False).first()
        if link:
            return 'host:' + str(link.linked_host_id)
    return key


def source_status(kind, obj, pref, now=None):
    now = now or timezone.now()
    if not pref or not pref.enabled:
        return 'disabled', '监控尚未开启'
    if not User.objects.filter(pk=pref.owner_id, is_active=True, is_supper=True, is_deleted=False).exists():
        return 'disabled', '开启监控的账号已停用或失去权限，请重新配置'
    # The digest is a successful snapshot identity, not an initial credential
    # baseline. A first failed check has none and must keep its actual reason.
    if pref.config_digest and pref.config_digest != config_digest(kind, obj):
        return 'invalidated', '来源配置已变化，请重新检查'
    if not pref.last_success_at:
        return pref.status, pref.message
    if pref.status != 'ok' or (now - pref.last_success_at).total_seconds() > pref.interval_hours * 3600:
        return 'stale', pref.message if pref.status != 'ok' else '检查结果已过期，请重新检查'
    return 'ok', pref.message


def source_view(kind, obj, pref=None):
    key = kind + ':' + str(obj.pk)
    groups = ([{'key': 'host:' + str(g.pk), 'kind': 'host', 'id': g.pk, 'name': g.name}
               for g in obj.groups.all()] if kind == 'host' else
              [{'key': 'panel:' + str(obj.group_id), 'kind': 'panel', 'id': obj.group_id, 'name': obj.group.name}])
    address = obj.hostname if kind == 'host' else urlsplit(obj.api_base_url or obj.url).hostname or ''
    status, message = source_status(kind, obj, pref)
    return {'key': key, 'kind': kind, 'id': obj.pk, 'name': obj.name, 'address': address, 'groups': groups,
            'enabled': bool(pref and pref.enabled), 'reminders': bool(pref and pref.reminders),
            'interval_hours': pref.interval_hours if pref else 6, 'revision': pref.revision if pref else '',
            'last_attempt_at': iso(pref.last_attempt_at) if pref else None,
            'last_success_at': iso(pref.last_success_at) if pref else None, 'status': status, 'message': message}


def sources(user):
    permission(user)
    prefs = {p.key: p for p in SSLSource.objects.all()}
    items = [source_view('host', obj, prefs.get('host:' + str(obj.pk))) for obj in Host.objects.order_by('id')]
    items += [source_view('panel', obj, prefs.get('panel:' + str(obj.pk)))
              for obj in Panel.objects.select_related('group').order_by('id')]
    return {'items': items}


def configure(user, data):
    permission(user)
    strict(data, ('key', 'revision', 'enabled', 'reminders', 'interval_hours'))
    if set(data) != {'key', 'revision', 'enabled', 'reminders', 'interval_hours'}:
        raise ValueError('请完整提供来源配置')
    if type(data['enabled']) is not bool or type(data['reminders']) is not bool:
        raise ValueError('开关必须是布尔值')
    if type(data['interval_hours']) is not int or data['interval_hours'] not in INTERVALS:
        raise ValueError('检查间隔必须为 1、6、12 或 24 小时')
    if not isinstance(data['revision'], str) or len(data['revision']) > 32:
        raise ValueError('配置版本无效')
    try:
        with transaction.atomic():
            current = permission(user, lock=True)
            kind, obj = source_object(data['key'], lock=True)
            pref = SSLSource.objects.select_for_update().filter(pk=data['key']).first()
            if (pref.revision if pref else '') != data['revision']:
                raise ValueError('来源配置已被修改，请刷新后重试')
            owner_active = bool(pref and User.objects.filter(pk=pref.owner_id, is_active=True,
                                                           is_supper=True, is_deleted=False).exists())
            if (pref and pref.enabled == data['enabled'] and pref.reminders == data['reminders'] and
                    pref.interval_hours == data['interval_hours'] and pref.config_digest == config_digest(kind, obj) and
                    owner_active):
                return source_view(kind, obj, pref)
            if pref and not owner_active:
                pref.status, pref.message = 'invalidated', '监控授权已变化，请重新检查'
            pref = pref or SSLSource(key=data['key'])
            pref.owner, pref.enabled, pref.reminders = current, data['enabled'], data['reminders']
            pref.interval_hours, pref.revision = data['interval_hours'], uuid.uuid4().hex
            pref.save()
            return source_view(kind, obj, pref)
    except IntegrityError:
        raise ValueError('来源配置已被修改，请刷新后重试') from None


def certificate_cycle(cert):
    return digest([cert.local_certificate.get('fingerprint'), cert.local_certificate.get('expires_at')])


def hidden_certificates(user):
    raw = UserSetting.objects.filter(user_id=user.pk, key=VISIBILITY_KEY).values_list('value', flat=True).first()
    if not isinstance(raw, str) or len(raw) > 1024 * 1024:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    if not isinstance(value, dict) or len(value) > MAX_HIDDEN_CERTIFICATES:
        return {}
    return {key: cycle for key, cycle in value.items() if re.fullmatch(r'[1-9][0-9]{0,18}', key)
            and isinstance(cycle, str) and re.fullmatch(r'[0-9a-f]{64}', cycle)}


def set_visibility(user, certificate_id, data):
    permission(user)
    strict(data, ('hidden', 'cycle'))
    if (type(data.get('hidden')) is not bool or not isinstance(data.get('cycle'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', data['cycle'])):
        raise ValueError('请提供显示设置和当前证书周期')
    with transaction.atomic():
        current = permission(user, lock=True)
        cert = SSLCertificate.objects.select_for_update().select_related('source').filter(
            pk=integer(certificate_id), present=True).first()
        if cert is None:
            raise ValueError('证书已不存在，请刷新列表')
        source = next((row for row in sources(current)['items'] if row['key'] == cert.source_id), None)
        if source is None:
            raise ValueError('证书来源已不存在，请刷新列表')
        cycle = certificate_cycle(cert)
        if data['cycle'] != cycle:
            raise ValueError('证书已更新，请刷新后重新操作')
        if data['hidden'] and certificate_view(cert, source)['state'] != 'expired':
            raise ValueError('仅可隐藏当前已过期的证书，请刷新列表')
        preferences = hidden_certificates(current)
        key = str(cert.pk)
        if data['hidden']:
            if key not in preferences and len(preferences) >= MAX_HIDDEN_CERTIFICATES:
                raise ValueError('隐藏记录已达上限，请先恢复部分记录')
            preferences[key] = cycle
        else:
            preferences.pop(key, None)
        UserSetting.objects.update_or_create(user=current, key=VISIBILITY_KEY,
            defaults={'value': json.dumps(preferences, separators=(',', ':'))})
    return {'id': cert.pk, 'hidden': data['hidden'], 'cycle': cycle}


def certificate_covers(domain, domains):
    if domain in domains:
        return True
    if domain.startswith('*.'):
        return False
    return any(san.startswith('*.') and domain.endswith(san[1:]) and domain.count('.') == san.count('.')
               for san in domains)


CONFIGURATION_MESSAGES = {
    'disabled': '宝塔未开启 SSL，网站主配置未绑定证书',
    'configuration_unreadable': '网站主配置无法完整核实，请检查读取权限和配置格式',
    'certificate_unreadable': '网站已开启 SSL，但公开证书读取或摘要核对失败',
    'status_conflict': '宝塔 SSL 开关与网站主配置不一致，请核对配置后重新巡检',
    'status_unknown': '宝塔未返回有效的 SSL 开关状态，请检查 API 响应后重新巡检',
    'deployment_unverified': '证书部署路径尚未完整核实，当前仅显示公开证书摘要',
    'verified': '',
}


def configuration_state(cert):
    plan = cert.plan if isinstance(cert.plan, dict) else {}
    value = plan.get('configuration_state')
    return value if isinstance(value, str) and value in CONFIGURATION_MESSAGES else ''


def certificate_renewal(cert):
    renew = safe_renewal(cert.renewal)
    configuration = configuration_state(cert)
    if configuration == 'disabled':
        renew.update(supported=False, reason='请先在宝塔申请并部署证书，再重新巡检')
    elif configuration in ('configuration_unreadable', 'certificate_unreadable', 'status_conflict', 'status_unknown'):
        renew.update(supported=False, reason=CONFIGURATION_MESSAGES[configuration])
    elif cert.domain.startswith('*.'):
        renew.update(supported=False, reason='通配符域名无法进行公网握手核验，请选择证书 SAN 覆盖的具体域名续签')
    elif not cert.deployment_verified:
        renew.update(supported=False, reason='证书部署路径尚未完整核实，暂不支持续签')
    return renew


def certificate_view(cert, source):
    local = safe_certificate(cert.local_certificate)
    public = safe_tls(cert.public_tls)
    stale = source['status'] != 'ok' or cert.config_digest != cert.source.config_digest
    expiry = local.get('expires_at')
    seconds = (instant(expiry) - utcnow()).total_seconds() if expiry else None
    days = seconds / 86400 if seconds is not None else None
    configuration = configuration_state(cert)
    if stale:
        state, message = 'stale', source['message']
    elif configuration == 'disabled':
        state, message = 'unconfigured', CONFIGURATION_MESSAGES[configuration]
    elif configuration in ('configuration_unreadable', 'certificate_unreadable', 'status_conflict', 'status_unknown'):
        state, message = 'unknown', CONFIGURATION_MESSAGES[configuration]
    elif seconds is None:
        state, message = 'unknown', '未读取到可解析证书，尚不能确认是否配置 SSL；请重新巡检核实'
    elif not cert.deployment_verified:
        state, message = 'unknown', '证书部署路径尚未完整核实，当前仅显示公开证书摘要'
    elif not certificate_covers(cert.domain, local.get('domains', [])):
        state, message = 'error', '本地部署证书的 SAN 未覆盖当前域名'
    elif seconds <= 0:
        state, message = 'expired', '本地部署证书已过期'
    elif days <= 10:
        state, message = 'expiring', '本地部署证书将在 10 天内到期'
    elif instant(local['not_before']) > utcnow():
        state, message = 'error', '本地部署证书尚未生效'
    elif cert.domain.startswith('*.'):
        state, message = 'unknown', '通配符仅展示本地证书，公网握手未核验'
    elif not public.get('verified') and not cert.domain.startswith('*.'):
        state, message = 'error', public['message']
    else:
        state, message = 'normal', '本地证书有效'
    if local and public.get('certificate') and local.get('fingerprint') != public['certificate'].get('fingerprint'):
        public['different_from_local'] = True
        if state == 'normal':
            message = '公网与本地证书不同，请分别核对 CDN 或代理部署'
    renew = certificate_renewal(cert)
    return {'id': cert.pk, 'source_key': source['key'], 'source_name': source['name'], 'domain': cert.domain,
            'cert_path': cert.cert_path,
            'port': cert.port, 'state': state, 'message': message, 'not_before': local.get('not_before'),
            'expires_at': expiry, 'days_remaining': days, 'sampled_at': iso(cert.sampled_at), 'stale': stale,
            'local_certificate': local, 'public_tls': public, 'renewal': renew,
            'latest_job_id': cert.latest_job_id}


def pagination(query):
    return integer(query.get('page', 1), 100000), integer(query.get('page_size', 20), 100)


def listing(user, query):
    current = permission(user)
    strict(query, ('q', 'source', 'state', 'group', 'page', 'page_size', 'hidden'))
    page, size = pagination(query)
    q, state, selected, group = query.get('q', ''), query.get('state', ''), query.get('source', ''), query.get('group', '')
    if not isinstance(q, str) or len(q) > 200 or any(ord(c) < 32 for c in q) or state and state not in STATES:
        raise ValueError('搜索或状态参数无效')
    if query.get('hidden', '') not in ('', '0', '1'):
        raise ValueError('显示筛选无效')
    hidden = hidden_certificates(current)
    all_sources = sources(user)['items']
    current_reminders = {r['certificate_id']: {'id': r['id'], 'muted': r['muted']}
                         for r in reminder_rows(user, all_sources)}
    filtered = [s for s in all_sources if (not selected or s['key'] == selected) and
                (not group or any(g['key'] == group for g in s['groups']))]
    by_key = {s['key']: s for s in filtered}
    rows = []
    for cert in SSLCertificate.objects.select_related('source').filter(source_id__in=by_key, present=True):
        src = by_key[cert.source_id]
        if q and q.lower() not in ' '.join((cert.domain, src['name'], src['address'])).lower():
            continue
        row = certificate_view(cert, src)
        if row['stale']:
            continue
        cycle = certificate_cycle(cert)
        rows.append({**row, 'reminder': current_reminders.get(cert.pk),
                     'cycle': cycle, 'hidden': hidden.get(str(cert.pk)) == cycle})
    hidden_count = sum(row['hidden'] for row in rows)
    rows = [row for row in rows if row['hidden'] == (query.get('hidden') == '1')]
    stats = {'total': len(rows), **{key: sum(r['state'] == key for r in rows)
                                  for key in ('normal', 'expiring', 'expired', 'unconfigured')}}
    stats['error'] = sum(r['state'] in ('error', 'stale', 'unknown') for r in rows)
    rows.sort(key=lambda r: (r['expires_at'] is None, r['expires_at'] or '', r['domain'], r['id']))
    rows = [r for r in rows if not state or r['state'] == state or state == 'error' and
            r['state'] in ('stale', 'unknown')]
    return {'items': rows[(page - 1) * size:page * size], 'total': len(rows), 'stats': stats, 'hidden_count': hidden_count,
            'sources': all_sources, 'active_jobs': [job_view(j) for j in SSLJob.objects.filter(owner_id=user.pk, state__in=ACTIVE)[:50]]}


def safe_certificate(value):
    if not value:
        return {}
    if not isinstance(value, dict) or not re.fullmatch('[a-fA-F0-9]{64}', str(value.get('fingerprint', ''))):
        raise ValueError('证书摘要无效')
    domains = value.get('domains')
    if not isinstance(domains, list) or len(domains) > 100:
        raise ValueError('证书域名超出限制')
    names = [domain_name(v) for v in domains]
    start, expiry = instant(value.get('not_before')), instant(value.get('expires_at'))
    return {'fingerprint': value['fingerprint'].lower(), 'domains': names,
            'not_before': start.isoformat(), 'expires_at': expiry.isoformat()}


def domain_name(value):
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError('证书域名无效')
    wildcard = value.startswith('*.')
    try:
        name = value[2:] if wildcard else value
        name = name.rstrip('.').encode('idna').decode('ascii').lower()
    except (UnicodeError, ValueError):
        raise ValueError('证书域名无效') from None
    if '.' not in name or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) or '..' in name:
        raise ValueError('证书域名无效')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in name.split('.')):
        raise ValueError('证书域名无效')
    return '*.' + name if wildcard else name


def safe_tls(value):
    if not isinstance(value, dict):
        return {'verified': False, 'certificate': {}, 'message': '公网检查尚未完成'}
    certificate = safe_certificate(value.get('certificate'))
    verified = value.get('verified') is True and bool(certificate)
    message = value.get('message')
    return {'verified': verified, 'certificate': certificate,
            'message': '公网证书验证通过' if verified else message if isinstance(message, str) and message in TLS_MESSAGES else
            '公网证书验证失败或无法连接，请查看本地证书与来源配置'}


def safe_summary(value):
    result = {}
    if not isinstance(value, dict):
        return result
    for key in ('source', 'domain', 'domains', 'cert_path', 'engine', 'validation', 'reload', 'impacts'):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 2048 and not any(ord(c) < 32 for c in item):
            result[key] = item
        elif isinstance(item, list) and len(item) <= 100 and all(isinstance(v, str) and len(v) <= 2048 and
                not any(ord(c) < 32 for c in v) for v in item):
            result[key] = item
    return result


def safe_renewal(value):
    value = value if isinstance(value, dict) else {}
    supported = value.get('supported') is True
    engine = value.get('engine') if value.get('engine') in ('baota', 'certbot', 'acme.sh') else ''
    reason = value.get('reason')
    reason = reason if isinstance(reason, str) and reason in RENEWAL_REASONS.values() else MESSAGES['unsupported_renewal']
    return {'engine': engine, 'supported': supported and bool(engine),
            'reason': '' if supported and engine else RENEWAL_REASONS.get(value.get('reason_code'), reason),
            'summary': safe_summary(value.get('summary'))}


def safe_result(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ('local_updated', 'public_updated', 'reload_verified')
              if type(value.get(key)) is bool}
    if 'local_certificate' in value:
        result['local_certificate'] = safe_certificate(value['local_certificate'])
    if 'public_tls' in value:
        result['public_tls'] = safe_tls(value['public_tls'])
    if isinstance(value.get('sources'), list):
        result['sources'] = []
        for row in value['sources'][:MAX_SOURCES]:
            if not isinstance(row, dict) or not isinstance(row.get('source_key'), str) or not re.fullmatch(r'(host|panel):[1-9][0-9]*', row['source_key']):
                continue
            item = {'source_key': row['source_key'], 'state': 'success' if row.get('state') == 'success' else 'failed'}
            if type(row.get('count')) is int and 0 <= row['count'] <= MAX_CERTIFICATES:
                item['count'] = row['count']
            if type(row.get('coverage_incomplete')) is bool:
                item['coverage_incomplete'] = row['coverage_incomplete']
            if row.get('message') in MESSAGES.values():
                item['message'] = row['message']
            result['sources'].append(item)
    return result


def job_view(job):
    return {key: getattr(job, key) for key in ('id', 'kind', 'state', 'phase', 'message', 'revision',
            'certificate_id', 'panel_task_id', 'acknowledgements')} | {
                'result': safe_result(job.result), 'summary': safe_summary(job.summary),
                'created_at': iso(job.created_at), 'updated_at': iso(job.updated_at)}


def owned_job(user, job_id, lock=False):
    permission(user, lock=lock)
    qs = SSLJob.objects.select_for_update() if lock else SSLJob.objects
    job = qs.filter(pk=integer(job_id), owner_id=user.pk).first()
    if job is None:
        raise PermissionError('任务不存在或无权访问')
    return job


def get_job(user, job_id):
    return job_view(owned_job(user, job_id))


def jobs_listing(user, query):
    permission(user)
    strict(query, ('page', 'page_size'))
    page, size = pagination(query)
    qs = SSLJob.objects.filter(owner_id=user.pk)
    return {'items': [job_view(j) for j in qs[(page - 1) * size:page * size]], 'total': qs.count()}


def reserve(key, job, now):
    SSLLease.objects.get_or_create(pk=key, defaults={'expires_at': now})
    token = uuid.uuid4().hex
    if not SSLLease.objects.filter(pk=key, expires_at__lte=now).update(
            job=job, token=token, expires_at=now + timedelta(seconds=LEASE_SECONDS)):
        raise ValueError('已有任务正在处理，请稍后再试')
    return token


def release(job):
    SSLLease.objects.filter(job_id=job.pk).update(job=None, token='', expires_at=timezone.now())


def enqueue_checks(user, data):
    permission(user)
    strict(data, ('source_keys',))
    keys = data.get('source_keys')
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_SOURCES or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('每次请选择 1 至 10 个不同来源')
    with transaction.atomic():
        current = permission(user, lock=True)
        now, versions = timezone.now(), {}
        if SSLJob.objects.filter(kind='check', state__in=('queued', 'running')).exists():
            raise ValueError('已有发现或检查任务在途，请稍后再试')
        for key in keys:
            kind, obj = source_object(key, lock=True)
            pref = SSLSource.objects.select_for_update().filter(pk=key, enabled=True).first()
            if pref is None:
                raise ValueError('请先明确开启所选来源的 SSL 监控')
            permission(pref.owner_id)
            if pref.last_attempt_at and (now - pref.last_attempt_at).total_seconds() < COOLDOWN:
                raise ValueError('手工检查冷却中，请 60 秒后重试')
            versions[key] = {'revision': pref.revision, 'digest': config_digest(kind, obj)}
        job = SSLJob.objects.create(owner=current, kind='check', source_keys=keys, source_versions=versions)
        reserve('check', job, now)
        SSLSource.objects.filter(pk__in=keys).update(last_attempt_at=now)
        return {'jobs': [job_view(job)]}


def reminder_rows(user, source_items=None):
    src = {row['key']: row for row in (sources(user)['items'] if source_items is None else source_items)}
    muted = set(SSLReminderPreference.objects.filter(user_id=user.pk, muted=True).values_list(
        'reminder__certificate_id', 'reminder__cycle'))
    rows = []
    seen = set()
    for reminder in SSLReminder.objects.select_related('certificate__source').order_by('-id'):
        cert = reminder.certificate
        source = src.get(cert.source_id)
        if (cert.pk in seen or not source or not source['enabled'] or not source['reminders'] or
                source['status'] != 'ok' or not cert.present or reminder.cycle != certificate_cycle(cert)):
            continue
        seen.add(cert.pk)
        rows.append({'id': reminder.pk, 'certificate_id': cert.pk, 'domain': cert.domain,
            'source_name': source['name'], 'stage': reminder.stage, 'expires_at': reminder.expires_at,
            'message': '证书已过期' if reminder.stage == 'expired' else '证书将在 ' + reminder.stage + ' 天内到期',
            'muted': (cert.pk, reminder.cycle) in muted})
    return rows


def reminders(user, query):
    permission(user)
    strict(query, ('muted', 'page', 'page_size'))
    page, size = pagination(query)
    if query.get('muted', '') not in ('', '0', '1'):
        raise ValueError('提醒筛选无效')
    rows = reminder_rows(user)
    unread = sum(not r['muted'] for r in rows)
    rows = [r for r in rows if r['muted'] == (query.get('muted') == '1')]
    return {'items': rows[(page - 1) * size:page * size], 'total': len(rows), 'unread_count': unread}


def mute_reminder(user, reminder_id, data):
    permission(user)
    strict(data, ('muted',))
    if type(data.get('muted')) is not bool:
        raise ValueError('请提供提醒开关')
    row = next((r for r in reminder_rows(user) if r['id'] == integer(reminder_id)), None)
    if row is None:
        raise ValueError('提醒已失效，请刷新')
    with transaction.atomic():
        permission(user, lock=True)
        reminder = SSLReminder.objects.get(pk=row['id'])
        # A preference anchored to any stage represents this account's complete
        # expiry cycle. Restore clears earlier-stage anchors as well.
        SSLReminderPreference.objects.filter(user_id=user.pk,
            reminder__certificate_id=reminder.certificate_id, reminder__cycle=reminder.cycle).update(muted=data['muted'])
        SSLReminderPreference.objects.update_or_create(user_id=user.pk, reminder_id=row['id'], defaults={'muted': data['muted']})
    return {**row, 'muted': data['muted']}
