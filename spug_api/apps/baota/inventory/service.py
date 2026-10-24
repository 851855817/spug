"""Local inventory views and explicit, globally serialized collection."""
import hashlib
import json
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from apps.account.models import User
from apps.baota.models import Panel, PanelGroup
from apps.baota.validation import positive_id
from .models import PanelInventoryLease as Lease, PanelInventorySnapshot as Snapshot
from .readers import (collect_panel, InventoryError, MESSAGES, MAX_ASSETS, MAX_DOMAINS,
                      DOMAIN, ASSET_NAME, SAFE_BASENAME)


LEASE_SECONDS = 75
COOLDOWN_SECONDS = 30
FRESH_SECONDS = 24 * 3600
KINDS = ('sites', 'backups')
SSL_STATES = ('valid', 'expiring', 'expired', 'unconfigured', 'unknown')
BACKUP_STATES = ('no_record', 'local_missing', 'empty', 'in_progress', 'panel_reported', 'unknown')
SITE_FIELDS = ('site_id', 'name', 'domains', 'php_version', 'site_status', 'ssl_state', 'expires_at')
BACKUP_FIELDS = ('asset_type', 'asset_id', 'name', 'backup_id', 'backup_name', 'backup_at', 'size_bytes',
                 'local_exists', 'in_progress', 'location', 'state')


def permission(user, lock=False):
    users = User.objects.select_for_update() if lock else User.objects
    if not users.filter(pk=getattr(user, 'pk', None), is_supper=True, is_active=True, is_deleted=False).exists():
        raise PermissionError('仅超级管理员可访问资产检查台')


def strict(data, allowed):
    if not isinstance(data, dict) and not hasattr(data, 'keys') or set(data) - set(allowed):
        raise ValueError('请求参数无效')
    if hasattr(data, 'getlist') and any(len(data.getlist(key)) != 1 for key in data):
        raise ValueError('请求参数重复')


def _configured(panel):
    return bool(panel.api_base_url and panel.api_key_ciphertext)


def config_digest(panel):
    fields = [panel.api_base_url, panel.api_key_ciphertext, panel.tls_ca, panel.tls_fingerprint,
              panel.api_config_revision, panel.api_version]
    return hashlib.sha256(json.dumps(fields, separators=(',', ':')).encode()).hexdigest()


def _ip(panel):
    try:
        return urlsplit(panel.api_base_url or panel.url).hostname or ''
    except ValueError:
        return ''


def panel_fields(panel):
    return {'panel_id': panel.pk, 'panel_name': panel.name, 'ip': _ip(panel),
            'group_id': panel.group_id, 'group_name': panel.group.name}


def _iso(value):
    return value.isoformat() if value else None


def _status(panel, snapshot, now):
    if not _configured(panel):
        return 'unconfigured', '宝塔 API 尚未配置'
    if snapshot is None:
        return 'never_collected', '尚未采集，请手动采集'
    if snapshot.config_revision != panel.api_config_revision or snapshot.config_digest != config_digest(panel):
        return 'invalidated', 'API 配置已变化，旧快照已失效，请重新采集'
    if not snapshot.sampled_at:
        return snapshot.status, snapshot.message
    if snapshot.status != 'ok':
        return 'stale', snapshot.message
    if (now - snapshot.sampled_at).total_seconds() > FRESH_SECONDS:
        return 'stale', '快照超过 24 小时，请重新采集'
    return 'ok', '采集成功（面板登记摘要）'


def panel_view(panel, snapshot, kind, now=None):
    now = now or timezone.now()
    status, message = _status(panel, snapshot, now)
    return {'panel_id': panel.pk, 'name': panel.name, 'ip': _ip(panel), 'group_id': panel.group_id,
            'configured': _configured(panel), 'kind': kind, 'status': status, 'message': message,
            'sampled_at': _iso(snapshot.sampled_at) if snapshot else None,
            'attempted_at': _iso(snapshot.attempted_at) if snapshot else None,
            'cooldown_until': _iso(snapshot.cooldown_until) if snapshot else None,
            'stale': bool(snapshot and snapshot.sampled_at and status != 'ok')}


def options(user):
    permission(user)
    return {'panels': [{'id': panel.pk, 'name': panel.name, 'ip': _ip(panel), 'group_id': panel.group_id,
                        'configured': _configured(panel)} for panel in Panel.objects.order_by('id')],
            'groups': [group.to_view() for group in PanelGroup.objects.all()],
            'limits': {'total_timeout_seconds': 45, 'requests': 200, 'assets': MAX_ASSETS,
                       'fresh_hours': 24, 'cooldown_seconds': COOLDOWN_SECONDS}}


def _group_ids(group_id):
    groups = list(PanelGroup.objects.values_list('id', 'parent_id'))
    if group_id not in {row[0] for row in groups}:
        raise ValueError('所选分组不存在')
    result = {group_id}
    while True:
        added = {pk for pk, parent in groups if parent in result} - result
        if not added:
            return result
        result.update(added)


def _ssl(row, now):
    expiry = row.get('expires_at')
    result = {'ssl_state': 'unknown', 'expires_at': '', 'days_remaining': None}
    if row.get('ssl_state') == 'unconfigured' and not expiry:
        result['ssl_state'] = 'unconfigured'
    elif isinstance(expiry, str):
        try:
            when = date.fromisoformat(expiry)
            days = (when - now.date()).days
            result.update(ssl_state='expired' if days < 0 else 'expiring' if days <= 30 else 'valid',
                          expires_at=expiry, days_remaining=days)
        except ValueError:
            pass
    return result


def _age(row, now, maximum):
    age, overdue = None, None
    if row.get('backup_id') is not None and isinstance(row.get('backup_at'), str):
        try:
            when = datetime.fromisoformat(row['backup_at'])
            seconds = (now - when).total_seconds()
            if seconds >= 0:
                age = round(seconds / 3600, 2)
                overdue = seconds > maximum * 3600
        except (ValueError, TypeError, OverflowError):
            pass
    return {'age_hours': age, 'overdue': overdue}


def listing(user, kind, query):
    permission(user)
    strict(query, ('page', 'page_size', 'q', 'panel_id', 'group_id', 'state') +
           (('max_age_hours',) if kind == 'backups' else ()))
    page, size = positive_id(query.get('page', 1)), positive_id(query.get('page_size', 20))
    if page > 100000 or size > 100:
        raise ValueError('分页范围无效')
    q = query.get('q', '')
    if not isinstance(q, str) or len(q) > 200 or any(ord(c) < 32 for c in q):
        raise ValueError('关键字格式无效')
    q = q.strip().lower()
    state, states = query.get('state', ''), SSL_STATES if kind == 'sites' else BACKUP_STATES
    if state and state not in states + (('overdue',) if kind == 'backups' else ()):
        raise ValueError('状态筛选无效')
    max_age = positive_id(query.get('max_age_hours', 48))
    if max_age not in (24, 48, 72, 168):
        raise ValueError('备份时间阈值必须为 24、48、72 或 168 小时')
    panels = Panel.objects.select_related('group').order_by('id')
    if query.get('panel_id'):
        panels = panels.filter(pk=positive_id(query['panel_id']))
    if query.get('group_id'):
        panels = panels.filter(group_id__in=_group_ids(positive_id(query['group_id'])))
    panels = list(panels)
    snapshots = {s.panel_id: s for s in Snapshot.objects.filter(panel_id__in=[p.pk for p in panels], kind=kind)}
    rows, statuses, now = [], [], timezone.now()
    for panel in panels:
        snapshot = snapshots.get(panel.pk)
        status = panel_view(panel, snapshot, kind, now)
        statuses.append(status)
        if snapshot is None or not snapshot.sampled_at or status['status'] in ('invalidated', 'unconfigured'):
            continue
        context = {**panel_fields(panel), 'sampled_at': status['sampled_at'], 'stale': status['stale']}
        for raw in _safe_rows(snapshot.rows, kind):
            # Select again when rendering, including snapshots produced by older code.
            fields = SITE_FIELDS if kind == 'sites' else BACKUP_FIELDS
            row = {key: raw.get(key) for key in fields}
            row.update(context)
            if kind == 'sites':
                row['id'] = f"{panel.pk}:{row['site_id']}"
                row.update(_ssl(row, now))
            else:
                row['id'] = f"{panel.pk}:{row['asset_type']}:{row['asset_id']}"
                row.update(_age(row, now, max_age))
            searchable = [str(row.get(key, '')) for key in ('name', 'panel_name', 'ip', 'group_name')]
            if kind == 'sites':
                searchable.extend(domain['name'] for domain in row.get('domains') or [])
            if q and not any(q in value.lower() for value in searchable):
                continue
            rows.append(row)
    key = 'ssl_state' if kind == 'sites' else 'state'
    summary = {'total': len(rows), **{value: sum(row[key] == value for row in rows) for value in states},
               'stale': sum(row['stale'] for row in rows)}
    if kind == 'backups':
        summary['overdue'] = sum(row['overdue'] is True for row in rows)
    filtered = [row for row in rows if not state or (row.get('overdue') is True if state == 'overdue' else row[key] == state)]
    return {'items': filtered[(page - 1) * size:page * size], 'total': len(filtered), 'page': page,
            'page_size': size, 'summary': summary, 'panels': statuses}


def acquire_lease(now):
    # get_or_create handles the singleton initialization race with a savepoint;
    # a conditional UPDATE is the sole admission authority across API processes.
    Lease.objects.get_or_create(slot=1, defaults={'expires_at': now})
    token = uuid.uuid4().hex
    claimed = Lease.objects.filter(pk=1, expires_at__lte=now).update(
        token=token, expires_at=now + timedelta(seconds=LEASE_SECONDS))
    return token if claimed else None


def release_lease(token):
    # An expired worker cannot release its successor's lease.
    Lease.objects.filter(pk=1, token=token).update(token='', expires_at=timezone.now())


def _transient(panel, snapshot, kind, status, message):
    return {**panel_view(panel, snapshot, kind), 'status': status, 'message': message}


def _safe_rows(rows, kind):
    if not isinstance(rows, list) or len(rows) > MAX_ASSETS:
        raise InventoryError('invalid_response')
    fields, result, seen, domain_count = SITE_FIELDS if kind == 'sites' else BACKUP_FIELDS, [], set(), 0
    for row in rows:
        if not isinstance(row, dict):
            raise InventoryError('invalid_response')
        selected = {key: row.get(key) for key in fields}
        name = row.get('name')
        if not isinstance(name, str) or not ASSET_NAME.fullmatch(name):
            raise InventoryError('invalid_response')
        if kind == 'sites':
            if type(row.get('site_id')) is not int or row['site_id'] <= 0:
                raise InventoryError('invalid_response')
            domains = row.get('domains')
            if not isinstance(domains, list):
                raise InventoryError('invalid_response')
            domain_count += len(domains)
            if domain_count > MAX_DOMAINS:
                raise InventoryError('limit')
            selected['domains'] = []
            for domain in domains:
                if (not isinstance(domain, dict) or not isinstance(domain.get('name'), str)
                        or not DOMAIN.fullmatch(domain['name']) or '..' in domain['name']
                        or type(domain.get('port')) is not int or not 1 <= domain['port'] <= 65535):
                    raise InventoryError('invalid_response')
                selected['domains'].append({'name': domain['name'], 'port': domain['port']})
            if (row.get('site_status') not in ('running', 'stopped', 'unknown')
                    or row.get('ssl_state') not in ('configured', 'unconfigured', 'unknown')
                    or not isinstance(row.get('php_version'), str) or len(row['php_version']) > 3
                    or not isinstance(row.get('expires_at'), str) or len(row['expires_at']) > 10):
                raise InventoryError('invalid_response')
        else:
            if (row.get('asset_type') not in ('site', 'database') or type(row.get('asset_id')) is not int
                    or row['asset_id'] <= 0 or row.get('state') not in BACKUP_STATES
                    or row.get('location') not in ('local', 'cloud_record', 'unknown')):
                raise InventoryError('invalid_response')
            for field in ('backup_id', 'size_bytes'):
                if row.get(field) is not None and (type(row[field]) is not int or not 0 <= row[field] < 2 ** 63):
                    raise InventoryError('invalid_response')
            for field in ('local_exists', 'in_progress'):
                if row.get(field) is not None and type(row[field]) is not bool:
                    raise InventoryError('invalid_response')
            if (not isinstance(row.get('backup_name'), str)
                    or row['backup_name'] and not SAFE_BASENAME.fullmatch(row['backup_name'])
                    or row.get('backup_at') is not None and
                    (not isinstance(row['backup_at'], str) or len(row['backup_at']) > 32)):
                raise InventoryError('invalid_response')
        identity = row.get('site_id') if kind == 'sites' else (row.get('asset_type'), row.get('asset_id'))
        if identity in seen:
            raise InventoryError('invalid_response')
        seen.add(identity)
        result.append(selected)
    if len(json.dumps(result).encode()) > 2 * 1024 * 1024:
        raise InventoryError('limit')
    return result


def collect(user, data):
    permission(user)
    strict(data, ('panel_id', 'kind'))
    panel_id, kind = positive_id(data.get('panel_id')), data.get('kind')
    if kind not in KINDS:
        raise ValueError('采集类型无效')
    panel = Panel.objects.select_related('group').filter(pk=panel_id).first()
    if panel is None:
        raise ValueError('面板不存在')
    cached = Snapshot.objects.filter(panel=panel, kind=kind).first()
    if not _configured(panel):
        return panel_view(panel, cached, kind)
    now = timezone.now()
    if cached and cached.cooldown_until and cached.cooldown_until > now:
        return _transient(panel, cached, kind, 'cooldown', '采集冷却中，请稍后再试')
    lease = acquire_lease(now)
    if lease is None:
        return _transient(panel, cached, kind, 'busy', '另一项资产采集正在进行，请稍后再试')
    try:
        # Re-read after admission. Network/crypto stays outside every transaction.
        permission(user)
        panel = Panel.objects.select_related('group').filter(pk=panel_id).first()
        if panel is None:
            raise ValueError('面板不存在')
        cached = Snapshot.objects.filter(panel=panel, kind=kind).first()
        if cached and cached.cooldown_until and cached.cooldown_until > timezone.now():
            return _transient(panel, cached, kind, 'cooldown', '采集冷却中，请稍后再试')
        if not _configured(panel):
            return panel_view(panel, cached, kind)
        digest, revision, attempted_at = config_digest(panel), panel.api_config_revision, timezone.now()
        rows, message = None, MESSAGES['collection_failed']
        try:
            rows = _safe_rows(collect_panel(panel, kind), kind)
            message = '采集成功（面板登记摘要）'
        except Exception as error:
            message = MESSAGES.get(getattr(error, 'code', ''), MESSAGES['collection_failed'])
        finished = timezone.now()
        with transaction.atomic():
            if not Lease.objects.select_for_update().filter(pk=1, token=lease, expires_at__gt=finished).exists():
                return _transient(panel, cached, kind, 'busy', '采集租约已过期，请重新采集')
            permission(user, lock=True)
            current = Panel.objects.select_for_update().select_related('group').filter(pk=panel_id).first()
            if current is None:
                raise ValueError('面板已删除，采集结果已丢弃')
            cached = Snapshot.objects.filter(panel=current, kind=kind).first()
            if current.api_config_revision != revision or config_digest(current) != digest:
                if cached:
                    cached.attempted_at = attempted_at
                    cached.cooldown_until = finished + timedelta(seconds=COOLDOWN_SECONDS)
                    cached.save(update_fields=['attempted_at', 'cooldown_until'])
                return _transient(current, cached, kind, 'invalidated', '采集期间 API 配置发生变化，结果已丢弃')
            saved, _ = Snapshot.objects.update_or_create(panel=current, kind=kind, defaults={
                'config_revision': revision if rows is not None or not cached else cached.config_revision,
                'config_digest': digest if rows is not None or not cached else cached.config_digest,
                'rows': rows if rows is not None else (cached.rows if cached else []),
                'sampled_at': finished if rows is not None else (cached.sampled_at if cached else None),
                'attempted_at': attempted_at, 'cooldown_until': finished + timedelta(seconds=COOLDOWN_SECONDS),
                'status': 'ok' if rows is not None else ('stale' if cached and cached.sampled_at else 'error'),
                'message': message})
        return panel_view(current, saved, kind)
    finally:
        release_lease(lease)
