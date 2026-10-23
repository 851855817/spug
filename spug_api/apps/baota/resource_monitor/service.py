"""Permission scoped, shared snapshots and database backed sampling admission."""
import hashlib
import json
import math
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.host.models import Host, Group
from apps.setting.models import Setting
from apps.baota.models import Panel
from apps.baota.validation import positive_id
from .models import ResourceMonitorPreference as Preference
from .models import ResourceMonitorSnapshot as Snapshot
from .models import ResourceMonitorLease as Lease


LEASE_SECONDS = 60  # Panel and SSH each have a 15s wall budget; total is at most 30s.
SOURCE_PERMISSIONS = {'host': 'host.host', 'panel': 'baota.panel'}
ERROR_MESSAGES = {
    'panel_unconfigured': '宝塔 API 尚未配置', 'host_unverified': '主机尚未验证',
    'auth_failed': '采集认证失败，请检查来源配置', 'timeout': '采集超时，请稍后重试',
    'unsupported': '暂不支持此系统', 'invalid_response': '采集数据格式无效',
    'collection_failed': '采集失败，请检查来源配置',
}


def collect_panel(panel):
    # Lazy imports also keep schema discovery independent of the transport.
    from .collector import collect_panel as collect
    return collect(panel)


def collect_host(host):
    from .collector import collect_host as collect
    return collect(host)


class Access:
    def __init__(self, user):
        self.user = user
        self.permissions = set() if user.is_supper else user.page_perms
        self.group_ids = None

    def has(self, *permissions):
        return self.user.is_supper or all(code in self.permissions for code in permissions)

    def require(self, *permissions):
        if not self.has(*permissions):
            raise ValueError('权限拒绝')

    def groups(self):
        if self.group_ids is None and not self.user.is_supper:
            allowed = set(self.user.group_perms)
            edges = list(Group.objects.values_list('id', 'parent_id'))
            while True:
                expanded = allowed | {pk for pk, parent in edges if parent in allowed}
                if expanded == allowed:
                    break
                allowed = expanded
            self.group_ids = allowed
        return self.group_ids

    def hosts(self):
        query = Host.objects.all()
        if not self.user.is_supper:
            query = query.filter(groups__id__in=self.groups()).distinct()
        return query

    def can_host(self, host_id):
        return self.has('host.host.view') and self.hosts().filter(pk=host_id).exists()


def _kind(value):
    if not isinstance(value, str) or value not in SOURCE_PERMISSIONS:
        raise ValueError('请选择有效来源')
    return value


def _source(access, kind, record_id, edit=False):
    kind = _kind(kind)
    prefix = SOURCE_PERMISSIONS[kind]
    access.require(prefix + '.view', *([prefix + '.edit'] if edit else []))
    query = (access.hosts().prefetch_related('groups') if kind == 'host' else
             Panel.objects.select_related('group'))
    source = query.filter(pk=positive_id(record_id)).first()
    if source is None:
        raise ValueError('来源不存在或无权访问')
    return source


def _host_groups(access, host):
    return [{'key': 'host:' + str(group.pk), 'name': group.name}
            for group in host.groups.all()
            if access.user.is_supper or group.pk in access.groups()]


def _address(source, kind):
    return source.hostname if kind == 'host' else (urlsplit(source.api_base_url or source.url).hostname or '')


def _source_meta(access, kind, source, preference, accessible_hosts):
    groups = (_host_groups(access, source) if kind == 'host' else
              [{'key': 'panel:' + str(source.group_id), 'name': source.group.name}])
    return {'id': source.pk, 'kind': kind, 'name': source.name, 'address': _address(source, kind),
            'enabled': bool(preference and preference.enabled),
            'group_ids': ([int(g['key'].split(':')[1]) for g in groups]), 'groups': groups,
            'linked_host_id': (preference.linked_host_id if preference and
                               preference.linked_host_id in accessible_hosts else None)}


def sources(user, kind):
    access, kind = Access(user), _kind(kind)
    access.require(SOURCE_PERMISSIONS[kind] + '.view')
    visible_hosts = (list(access.hosts().prefetch_related('groups'))
                     if access.has('host.host.view') else [])
    host_ids = {h.pk for h in visible_hosts}
    records = visible_hosts if kind == 'host' else list(Panel.objects.select_related('group'))
    prefs = {getattr(p, kind + '_id'): p for p in
             Preference.objects.filter(**{kind + '_id__in': [r.pk for r in records]})}
    return {'sources': [_source_meta(access, kind, source, prefs.get(source.pk), host_ids)
                        for source in records],
            'hosts': [{'id': host.pk, 'name': host.name, 'address': host.hostname}
                      for host in visible_hosts] if access.has('host.host.edit') else []}


def _invalidate(preference):
    keys = {('host:' + str(preference.host_id)) if preference.host_id else
            ('panel:' + str(preference.panel_id))}
    if preference.linked_host_id:
        keys.add('host:' + str(preference.linked_host_id))
    Snapshot.objects.filter(Q(card_key__in=keys) | Q(target_key__in=keys)).delete()


def settings(user, data):
    kind = _kind(data.get('kind'))
    if type(data.get('enabled')) is not bool:
        raise ValueError('监控状态必须为布尔值')
    access = Access(user)
    source = _source(access, kind, data.get('id'), edit=True)
    with transaction.atomic():
        preference, _ = Preference.objects.get_or_create(**{kind: source})
        preference = Preference.objects.select_for_update().get(pk=preference.pk)
        if preference.enabled != data['enabled']:
            preference.enabled = data['enabled']
            preference.revision = uuid.uuid4().hex
            preference.save(update_fields=['enabled', 'revision'])
            _invalidate(preference)
    accessible_hosts = ({preference.linked_host_id} if preference.linked_host_id and
                         access.can_host(preference.linked_host_id) else set())
    return _source_meta(access, kind, source, preference, accessible_hosts)


def link(user, data):
    access = Access(user)
    panel = _source(access, 'panel', data.get('panel_id'), edit=True)
    # null explicitly means unlink; missing, booleans and strings are rejected.
    if 'host_id' not in data or (data['host_id'] is not None and type(data['host_id']) is not int):
        raise ValueError('请选择有效主机或取消关联')
    host = _source(access, 'host', data['host_id'], edit=True) if data['host_id'] is not None else None
    with transaction.atomic():
        preference, _ = Preference.objects.get_or_create(panel=panel)
        preference = Preference.objects.select_for_update().get(pk=preference.pk)
        if preference.linked_host_id:
            _source(access, 'host', preference.linked_host_id, edit=True)
        else:
            access.require('host.host.view', 'host.host.edit')
        if preference.linked_host_id != (host.pk if host else None):
            _invalidate(preference)
            preference.linked_host = host
            preference.revision = uuid.uuid4().hex
            preference.save(update_fields=['linked_host', 'revision'])
            _invalidate(preference)
    return {'ok': True}


def _private_key():
    # Do not use AppSetting.get's process local lru cache for configuration validity.
    setting = Setting.objects.filter(key='private_key').first()
    return setting.real_val if setting else ''


def _digest(card, global_key):
    values = []
    for pref in card['_preferences']:
        if pref.panel_id:
            p = pref.panel
            config = [p.api_base_url, p.api_key_ciphertext, p.tls_ca, p.tls_fingerprint, p.api_config_revision]
        else:
            h = pref.host
            config = [h.hostname, h.port, h.username, h.pkey or global_key, h.is_verified]
        values.append([pref.pk, pref.revision, pref.linked_host_id, config])
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _cards(access, key=None):
    access.require('monitor.monitor.view')
    allowed = Q(pk__in=[])
    if access.has('baota.panel.view'):
        allowed |= Q(panel__isnull=False)
    host_ids = set(access.hosts().values_list('id', flat=True)) if access.has('host.host.view') else set()
    if host_ids:
        allowed |= Q(host_id__in=host_ids)
    query = Preference.objects.filter(allowed, enabled=True)
    if key is not None:
        try:
            kind, record_id = key.split(':', 1)
            record_id = positive_id(record_id)
        except (ValueError, AttributeError):
            return []
        if kind == 'host':
            query = query.filter(Q(host_id=record_id) | Q(linked_host_id=record_id))
        elif kind == 'panel':
            linked_host_id = Preference.objects.filter(panel_id=record_id).values_list('linked_host_id', flat=True).first()
            query = query.filter(Q(panel_id=record_id) | Q(host_id=linked_host_id, host__isnull=False))
        else:
            return []
    preferences = list(query
                       .select_related('host', 'panel', 'panel__group').prefetch_related('host__groups')
                       .order_by('pk'))
    result = {}
    for pref in preferences:
        if pref.host_id:
            card_key = 'host:' + str(pref.host_id)
            result[card_key] = {'key': card_key, 'name': pref.host.name, 'address': pref.host.hostname,
                                'groups': _host_groups(access, pref.host), 'sources': [], 'snapshot': None,
                                'can_link': False, 'panel_id': None, 'linked_host_id': None,
                                '_preferences': [], '_target': card_key}
    for pref in preferences:
        if pref.panel_id:
            host_key = 'host:' + str(pref.linked_host_id)
            card_key = host_key if host_key in result else 'panel:' + str(pref.panel_id)
            if card_key not in result:
                result[card_key] = {'key': card_key, 'name': pref.panel.name,
                                   'address': _address(pref.panel, 'panel'), 'groups': [], 'sources': [],
                                   'snapshot': None, 'can_link': False, 'panel_id': None,
                                   'linked_host_id': None, '_preferences': [],
                                   '_target': host_key if pref.linked_host_id else card_key}
            card = result[card_key]
            card['groups'].append({'key': 'panel:' + str(pref.panel.group_id), 'name': pref.panel.group.name})
            if card['panel_id'] is None:
                card['panel_id'] = pref.panel_id
                card['linked_host_id'] = pref.linked_host_id if pref.linked_host_id in host_ids else None
            card['can_link'] = access.has('baota.panel.edit', 'host.host.view', 'host.host.edit') and (
                not pref.linked_host_id or pref.linked_host_id in host_ids)
            card['_preferences'].append(pref)
            card['sources'].append({'kind': 'panel', 'id': pref.panel_id, 'name': pref.panel.name})
        else:
            card = result['host:' + str(pref.host_id)]
            card['_preferences'].append(pref)
            card['sources'].append({'kind': 'host', 'id': pref.host_id, 'name': pref.host.name})
    selected = [card for card in result.values() if key is None or key == card['key']]
    global_key = _private_key() if any(p.host_id and not p.host.pkey for c in selected
                                     for p in c['_preferences']) else ''
    for card in selected:
        card['_digest'] = _digest(card, global_key)
        card['_uses_global_key'] = any(p.host_id and not p.host.pkey for p in card['_preferences'])
        card['groups'] = list({g['key']: g for g in card['groups']}.values())
        for pref in card['_preferences']:
            if pref.host_id and not pref.host.pkey:
                pref.host.pkey = global_key
    return selected


def snapshot_view(snapshot):
    if snapshot is None:
        return None
    result = {field: getattr(snapshot, field) for field in
              ('status', 'message', 'metrics', 'source_kind')}
    result.update({field: getattr(snapshot, field).timestamp() if getattr(snapshot, field) else None
                   for field in ('sampled_at', 'attempted_at')})
    return result


def cards(user):
    records = _cards(Access(user))
    cached = Snapshot.objects.in_bulk([card['_digest'] for card in records])
    return {'cards': [{**{k: v for k, v in card.items() if not k.startswith('_')},
                       'snapshot': snapshot_view(cached.get(card['_digest']))} for card in records],
            'default_interval': 60}


def acquire_lease(target_key, now):
    """Conditional UPDATE and unique target_key are the cross-process authorities.

    An expired target is reclaimed in place, so its old unique key never prevents
    a new token. Each individual operation commits before network activity.
    """
    token, expires = uuid.uuid4().hex, now + timedelta(seconds=LEASE_SECONDS)
    with transaction.atomic():
        for slot in (1, 2):
            Lease.objects.get_or_create(slot=slot, defaults={'expires_at': now})
        current = Lease.objects.filter(target_key=target_key).first()
        if current:
            claimed = Lease.objects.filter(pk=current.pk, expires_at__lte=now).update(token=token, expires_at=expires)
            return (current.pk, token) if claimed else None
        for slot in (1, 2):
            try:
                with transaction.atomic():
                    if Lease.objects.filter(pk=slot, expires_at__lte=now).update(
                            target_key=target_key, token=token, expires_at=expires):
                        return slot, token
            except IntegrityError:
                # Another process has claimed this same target through the other slot.
                return None
    return None


def release_lease(slot, token):
    Lease.objects.filter(pk=slot, token=token).update(target_key=None, token='', expires_at=timezone.now())


def _retry(snapshot, interval, now):
    if snapshot is None:
        return 0
    due = max(snapshot.attempted_at + timedelta(seconds=30), snapshot.next_attempt_at)
    if not snapshot.failures and snapshot.sampled_at:
        due = max(due, snapshot.sampled_at + timedelta(seconds=interval))
    return max(0, math.ceil((due - now).total_seconds()))


def _shared_retry(target_key, now):
    # Visibility changes cannot multiply requests or reset failure backoff. Other
    # source sets contribute admission state only; their metrics never leave here.
    recent = _latest_attempt(target_key)
    if recent is None:
        return 0, 'cache'
    due = max(recent.attempted_at + timedelta(seconds=30), recent.next_attempt_at)
    return max(0, math.ceil((due - now).total_seconds())), 'backoff' if recent.failures else 'cache'


def _retry_state(cached, interval, target_key, now):
    local_wait = _retry(cached, interval, now)
    shared_wait, shared_reason = _shared_retry(target_key, now)
    reason = ('backoff' if (local_wait and cached and cached.failures) or
              (shared_wait and shared_reason == 'backoff') else 'cache')
    return max(local_wait, shared_wait), reason


def _latest_attempt(target_key):
    return (Snapshot.objects.filter(target_key=target_key).only('attempted_at', 'next_attempt_at', 'failures')
            .order_by('-attempted_at').first())


def _number(value, maximum=2 ** 63 - 1, integer=False):
    if type(value) not in (int, float) or not 0 <= value <= maximum or not math.isfinite(value):
        return None
    if integer and int(value) != value:
        return None
    return int(value) if integer else value


def clean_metrics(raw):
    """Persist only this schema, even if a transport accidentally returns extra data."""
    raw = raw if isinstance(raw, dict) else {}
    def fields(name, specification):
        value = raw.get(name)
        value = value if isinstance(value, dict) else {}
        return {key: _number(value.get(key), **options) for key, options in specification.items()}
    result = {
        'cpu': fields('cpu', {'percent': {'maximum': 100}, 'cores': {'maximum': 1048576, 'integer': True}}),
        'memory': fields('memory', {'used_bytes': {}, 'total_bytes': {}, 'percent': {'maximum': 100}}),
        'network': fields('network', {'up_bytes_sec': {}, 'down_bytes_sec': {}}),
        'load': [_number(x) for x in (raw.get('load', []) if isinstance(raw.get('load'), list) else [])[:3]],
        'disks': [],
    }
    result['load'] += [None] * (3 - len(result['load']))
    for disk in (raw.get('disks', []) if isinstance(raw.get('disks'), list) else [])[:32]:
        if not isinstance(disk, dict) or not isinstance(disk.get('path'), str):
            continue
        path = disk['path']
        if not path.startswith('/') or len(path) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in path):
            continue
        result['disks'].append({'path': path, 'used_bytes': _number(disk.get('used_bytes')),
                                'total_bytes': _number(disk.get('total_bytes')),
                                'percent': _number(disk.get('percent'), maximum=100)})
    return result


def _current(user, key):
    records = _cards(Access(user), key)
    return records[0] if records else None


def sample(user, data):
    key, interval = data.get('key'), data.get('interval', 60)
    if type(interval) is not int or interval not in (30, 60, 300):
        raise ValueError('采样间隔必须为 30、60 或 300 秒')
    if not isinstance(key, str) or len(key) > 64:
        raise ValueError('请选择有效卡片')
    card = _current(user, key)
    if card is None:
        raise ValueError('来源未开启或无权访问')
    now = timezone.now()
    cached = Snapshot.objects.filter(pk=card['_digest']).first()
    retry, reason = _retry_state(cached, interval, card['_target'], now)
    if retry:
        fresh = _current(user, key)
        return {'key': key, 'snapshot': snapshot_view(cached) if fresh and
                fresh['_digest'] == card['_digest'] else None, 'retry_after': retry, 'retry_reason': reason}
    lease = acquire_lease(card['_target'], now)
    if lease is None:
        earliest = Lease.objects.filter(expires_at__gt=now).order_by('expires_at').first()
        retry = max(1, math.ceil((earliest.expires_at - now).total_seconds())) if earliest else 1
        fresh = _current(user, key)
        return {'key': key, 'snapshot': snapshot_view(cached) if fresh and
                fresh['_digest'] == card['_digest'] else None, 'retry_after': retry, 'retry_reason': 'busy'}
    try:
        # A concurrent caller may have completed between our initial read and admission.
        fresh = _current(user, key)
        if fresh is None or fresh['_digest'] != card['_digest']:
            return {'key': key, 'snapshot': None, 'retry_after': 1, 'retry_reason': 'busy'}
        cached = Snapshot.objects.filter(pk=card['_digest']).first()
        retry, reason = _retry_state(cached, interval, card['_target'], timezone.now())
        if retry:
            return {'key': key, 'snapshot': snapshot_view(cached), 'retry_after': retry, 'retry_reason': reason}
        metrics, source_kind, message = None, '', ERROR_MESSAGES['collection_failed']
        # One panel and at most one SSH fallback keep the complete budget below 60s.
        ordered = sorted(card['_preferences'], key=lambda p: (p.panel_id is None, p.pk))
        candidates = [next((p for p in ordered if p.panel_id), None),
                      next((p for p in ordered if p.host_id), None)]
        for pref in [p for p in candidates if p is not None]:
            fresh = _current(user, key)
            if fresh is None or fresh['_digest'] != card['_digest']:
                return {'key': key, 'snapshot': None, 'retry_after': 1, 'retry_reason': 'busy'}
            try:
                raw = collect_panel(pref.panel) if pref.panel_id else collect_host(pref.host)
                metrics = clean_metrics(raw)
                source_kind = 'panel' if pref.panel_id else 'host'
                message = '采集成功'
                break
            except Exception as exc:
                message = ERROR_MESSAGES.get(getattr(exc, 'code', ''), ERROR_MESSAGES['collection_failed'])
        finished = timezone.now()
        with transaction.atomic():
            live_lease = Lease.objects.select_for_update().filter(pk=lease[0], token=lease[1],
                                                                 expires_at__gt=finished).first()
            if live_lease is None:
                return {'key': key, 'snapshot': None, 'retry_after': 1, 'retry_reason': 'busy'}
            # Lock sources as well as preferences to serialize credential/config writes.
            panel_ids = [p.panel_id for p in card['_preferences'] if p.panel_id]
            host_ids = [p.host_id for p in card['_preferences'] if p.host_id]
            list(Panel.objects.select_for_update().filter(pk__in=panel_ids).order_by('pk'))
            list(Host.objects.select_for_update().filter(pk__in=host_ids).order_by('pk'))
            if card['_uses_global_key']:
                list(Setting.objects.select_for_update().filter(key='private_key'))
            list(Preference.objects.select_for_update().filter(pk__in=[p.pk for p in card['_preferences']])
                 .order_by('pk'))
            fresh = _current(user, key)
            if fresh is None or fresh['_digest'] != card['_digest']:
                return {'key': key, 'snapshot': None, 'retry_after': 1, 'retry_reason': 'busy'}
            recent = _latest_attempt(card['_target'])
            failures = (recent.failures if recent else 0) + 1 if metrics is None else 0
            delay = (60, 120, 240, 300)[min(failures - 1, 3)] if failures else 30
            saved, _ = Snapshot.objects.update_or_create(config_digest=card['_digest'], defaults={
                'card_key': key, 'target_key': card['_target'],
                'metrics': metrics if metrics is not None else (cached.metrics if cached else clean_metrics({})),
                'status': 'ok' if metrics is not None else ('stale' if cached and cached.sampled_at else 'error'),
                'message': message, 'source_kind': source_kind or (cached.source_kind if cached else ''),
                'sampled_at': finished if metrics is not None else (cached.sampled_at if cached else None),
                'attempted_at': finished, 'next_attempt_at': finished + timedelta(seconds=delay), 'failures': failures,
            })
        return {'key': key, 'snapshot': snapshot_view(saved), 'retry_after': delay if failures else interval,
                'retry_reason': 'backoff' if failures else 'collected'}
    finally:
        release_lease(*lease)
