"""Strict local API and transactional metadata updates."""
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction, connection
from django.db.models import Q
from django.utils import timezone

from apps.account.models import User
from apps.host.models import Host, HostExtend
from apps.baota.models import Panel, ResourceMonitorPreference
from . import assets, dates
from .models import ExpiryMetadata, ExpiryAudit, ExpiryLock, ExpiryCheck
from .transactions import retry_lock_conflicts


EDIT_FIELDS = {'version', 'manual_expiry', 'owner_id', 'followup', 'reminder_enabled', 'exempt', 'note'}
STATES = {'expired', 'within_7', 'within_30', 'valid', 'unset', 'invalid', 'exempt'}


def require_admin(user, lock=False):
    query = User.objects.select_for_update() if lock else User.objects
    admin = query.filter(pk=getattr(user, 'pk', None), is_active=True, is_deleted=False, is_supper=True).first()
    if admin is None:
        raise PermissionError('仅活跃超级管理员可访问到期管理')
    return admin


def strict(values, allowed):
    if not hasattr(values, 'keys') or set(values) - set(allowed):
        raise ValueError('请求包含不支持的字段')
    if hasattr(values, 'getlist') and any(len(values.getlist(k)) != 1 for k in values):
        raise ValueError('请求参数不能重复')


def positive(value):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,18}', value, re.ASCII):
        number = int(value)
    else:
        raise ValueError('请选择有效的正整数标识或分页参数')
    if not 1 <= number <= 9223372036854775807:
        raise ValueError('标识或分页参数超出范围')
    return number


def pagination(params):
    page, page_size = positive(params.get('page', 1)), positive(params.get('page_size', 20))
    if page_size > 100:
        raise ValueError('每页最多显示 100 项')
    return page, page_size


def stamp(value):
    return value.isoformat(timespec='seconds') if value else None


def check_status():
    check = ExpiryCheck.objects.filter(pk=1).first()
    if check is None:
        return {'status': 'never', 'last_attempt_at': None, 'last_success_at': None, 'message': '尚未执行每日检查'}
    status, message = check.status, check.message
    if check.last_attempt_at and timezone.now() - check.last_attempt_at > timedelta(hours=36):
        status, message = 'stale', '超过一天未执行检查，请核对调度服务'
    return {'status': status, 'last_attempt_at': stamp(check.last_attempt_at),
            'last_success_at': stamp(check.last_success_at), 'message': message}


def options(user):
    require_admin(user)
    return {'owners': [{'id': owner.pk, 'name': owner.nickname or owner.username}
                       for owner in User.objects.filter(is_active=True, is_deleted=False).order_by('pk')],
            'groups': assets.group_options(), 'timezone': settings.TIME_ZONE, 'today': dates.platform_today().isoformat(),
            'schedule': '09:00', 'check': check_status()}


def listing(user, params):
    require_admin(user)
    strict(params, ('page', 'page_size', 'q', 'source', 'group', 'owner_id', 'state', 'followup'))
    page, page_size = pagination(params)
    query = params.get('q', '')
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('关键字最多 200 字')
    source, state, followup = params.get('source'), params.get('state'), params.get('followup')
    if source is not None and source not in ('host', 'panel'):
        raise ValueError('来源筛选无效')
    if state is not None and state not in STATES:
        raise ValueError('到期状态筛选无效')
    if followup is not None and followup not in ('pending', 'contacted'):
        raise ValueError('跟进状态筛选无效')
    owner_id = positive(params['owner_id']) if 'owner_id' in params else None
    groups = assets.descendants(params['group']) if 'group' in params else None
    rows = list(assets.inventory().values())
    query = query.strip().casefold()
    rows = [row for row in rows if
            (not query or query in (row['name'] + ' ' + row['address'] + ' ' +
                                    ' '.join(s['name'] for s in row['sources'])).casefold()) and
            (source is None or any(s['kind'] == source for s in row['sources'])) and
            (groups is None or any(g['key'] in groups for g in row['groups'])) and
            (owner_id is None or row['owner'] and row['owner']['id'] == owner_id) and
            (followup is None or row['followup'] == followup)]
    summary = {name: 0 for name in ('total', 'expired', 'within_7', 'within_30', 'unset', 'invalid', 'exempt')}
    summary['total'] = len(rows)
    for row in rows:
        if row['state'] in ('expired', 'unset', 'invalid', 'exempt'):
            summary[row['state']] += 1
        elif row['days_remaining'] is not None:
            summary['within_7'] += int(0 <= row['days_remaining'] <= 7)
            summary['within_30'] += int(0 <= row['days_remaining'] <= 30)
    if state in ('within_7', 'within_30'):
        limit = 7 if state == 'within_7' else 30
        rows = [r for r in rows if r['state'] not in ('exempt', 'invalid', 'unset') and
                r['days_remaining'] is not None and 0 <= r['days_remaining'] <= limit]
    elif state:
        rows = [r for r in rows if r['state'] == state]
    rows.sort(key=lambda r: (r['state'] in ('unset', 'invalid', 'exempt'),
                             r['days_remaining'] if r['days_remaining'] is not None else 10**9, r['key']))
    from .reminders import unread_count
    offset = (page - 1) * page_size
    return {'items': rows[offset:offset + page_size], 'total': len(rows), 'page': page, 'page_size': page_size,
            'summary': summary, 'unread_count': unread_count(user.pk), 'check': check_status(),
            'timezone': settings.TIME_ZONE, 'today': dates.platform_today().isoformat()}


def lock_decisions():
    ExpiryLock.objects.get_or_create(pk=1)
    ExpiryLock.objects.select_for_update().get(pk=1)


def lock_asset(key, resolve=True):
    """Lock rows used by existing association writes, including FK targets for new links."""
    def source_locks(model):
        # Existing monitor commits lock panels before hosts. Link changes instead
        # lock preferences before their host FK; NOWAIT avoids completing that
        # reverse wait cycle and lets our entire transaction retry safely.
        return model.objects.select_for_update(nowait=connection.features.has_select_for_update_nowait)

    kind, identifier = assets.parse_key(key)
    if kind == 'host':
        panel_ids = list(ResourceMonitorPreference.objects.filter(linked_host_id=identifier)
                         .values_list('panel_id', flat=True))
        list(source_locks(Panel).filter(pk__in=panel_ids).order_by('pk'))
        if not source_locks(Host).filter(pk=identifier).exists():
            raise ValueError('资产不存在，请刷新后重试')
        list(source_locks(HostExtend).filter(host_id=identifier))
        list(source_locks(ResourceMonitorPreference).filter(
            Q(host_id=identifier) | Q(linked_host_id=identifier) | Q(panel_id__in=panel_ids)).order_by('pk'))
    else:
        if not source_locks(Panel).filter(pk=identifier).exists():
            raise ValueError('资产不存在，请刷新后重试')
        list(source_locks(ResourceMonitorPreference).filter(panel_id=identifier).order_by('pk'))
    list(ExpiryMetadata.objects.select_for_update().filter(asset_key=key))
    if not resolve:
        return None
    row = assets.inventory().get(key)
    if row is None:
        raise ValueError('资产关联已变化，请刷新后编辑当前资产')
    return row


def validate_note(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError('备注必须为不超过 2000 字的文字')
    return value


def validate_version(version):
    if not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{64}', version, re.ASCII):
        raise ValueError('资产版本无效，请刷新后重试')


def validate_owner(identifier):
    if identifier is None:
        return
    if type(identifier) is not int:
        raise ValueError('负责人标识无效')
    positive(identifier)
    if not User.objects.select_for_update().filter(pk=identifier, is_active=True, is_deleted=False).exists():
        raise ValueError('负责人已停用或不存在，请重新选择')


@retry_lock_conflicts
def save(user, key, data, renew=False):
    strict(data, ('version', 'expires_on', 'note') if renew else EDIT_FIELDS)
    expected = {'version', 'expires_on', 'note'} if renew else EDIT_FIELDS
    if set(data) != expected:
        raise ValueError('请完整填写到期管理信息')
    assets.parse_key(key)
    validate_version(data['version'])
    validate_note(data['note'])
    if renew:
        new_date = dates.parse_manual(data['expires_on'])
    else:
        new_date = dates.parse_manual(data['manual_expiry']) if data['manual_expiry'] is not None else None
        if data['followup'] not in ('pending', 'contacted'):
            raise ValueError('跟进状态无效')
        if type(data['reminder_enabled']) is not bool or type(data['exempt']) is not bool:
            raise ValueError('提醒与无需管理必须为布尔值')
    with transaction.atomic():
        lock_decisions()
        actor = require_admin(user, lock=True)
        row = lock_asset(key)
        if row['version'] != data['version']:
            raise ValueError('资产记录、到期日或关联已变化，请刷新后重试')
        metadata, _ = ExpiryMetadata.objects.get_or_create(asset_key=key)
        before = assets.metadata_values(metadata)
        if renew:
            existing = dates.parse_original(row['expires_on'])
            if new_date <= dates.platform_today() or (existing and new_date <= existing):
                raise ValueError('续费到期日必须晚于今天及当前有效到期日')
            metadata.manual_expiry, metadata.followup, metadata.note = new_date, 'pending', data['note']
        else:
            validate_owner(data['owner_id'])
            metadata.manual_expiry = new_date
            for field in ('owner_id', 'followup', 'reminder_enabled', 'exempt', 'note'):
                setattr(metadata, field, data[field])
        metadata.revision = uuid.uuid4().hex
        metadata.save()
        ExpiryAudit.objects.create(asset_key=key, event='renew' if renew else 'update',
                                   actor_id=actor.pk, actor_name=actor.nickname or actor.username,
                                   detail={'before': before, 'after': assets.metadata_values(metadata)})
        return assets.inventory()[key]


def history(user, key, params):
    require_admin(user)
    assets.parse_key(key)
    strict(params, ('page', 'page_size'))
    page, page_size = pagination(params)
    query = ExpiryAudit.objects.filter(asset_key=key)
    offset = (page - 1) * page_size
    return {'items': [{'id': audit.pk, 'event': audit.event, 'actor_name': audit.actor_name,
                       'created_at': stamp(audit.created_at), 'detail': audit.detail}
                      for audit in query[offset:offset + page_size]],
            'total': query.count(), 'page': page, 'page_size': page_size}
