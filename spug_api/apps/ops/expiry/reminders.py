"""Durable administrator-only reminders. This module performs no external delivery."""
from django.db import transaction, connections
from django.utils import timezone

from . import assets, dates, service
from .models import ExpiryReminder, ExpiryReminderRead, ExpiryCheck
from .transactions import retry_lock_conflicts


def unread_count(user_id):
    return ExpiryReminder.objects.exclude(pk__in=ExpiryReminderRead.objects.filter(user_id=user_id)
                                         .values('reminder_id')).count()


def is_current(reminder, inventory):
    row = inventory.get(reminder.asset_key)
    return bool(row and row['reminder_enabled'] and not row['exempt'] and
                row['expires_on'] == reminder.expires_on.isoformat() and
                dates.reminder_stage(row['days_remaining']) == reminder.stage)


def listing(user, params):
    service.require_admin(user)
    service.strict(params, ('page', 'page_size', 'unread', 'current'))
    page, page_size = service.pagination(params)
    if any(params[name] != '1' for name in ('unread', 'current') if name in params):
        raise ValueError('提醒筛选只接受 1')
    inventory = assets.inventory()
    read = set(ExpiryReminderRead.objects.filter(user_id=user.pk).values_list('reminder_id', flat=True))
    query = ExpiryReminder.objects.all()
    if 'unread' in params:
        query = query.exclude(pk__in=read)
    offset = (page - 1) * page_size
    if 'current' in params:
        current_assets = {key: row for key, row in inventory.items() if row['reminder_enabled'] and
                          not row['exempt'] and dates.reminder_stage(row['days_remaining'])}
        candidates = query.filter(asset_key__in=current_assets,
                                  expires_on__in={r['expires_on'] for r in current_assets.values()})
        selected = [reminder for reminder in candidates if is_current(reminder, inventory)]
        total = len(selected)
        selected = selected[offset:offset + page_size]
    else:
        total = query.count()
        selected = query[offset:offset + page_size]
    rows = []
    for reminder in selected:
        unread, current = reminder.pk not in read, is_current(reminder, inventory)
        if ('unread' in params and not unread) or ('current' in params and not current):
            continue
        rows.append({'id': reminder.pk, 'asset_key': reminder.asset_key, 'name': reminder.name,
                     'address': reminder.address, 'expires_on': reminder.expires_on.isoformat(),
                     'stage': reminder.stage, 'created_at': service.stamp(reminder.created_at),
                     'unread': unread, 'current': current})
    return {'items': rows, 'total': total, 'page': page, 'page_size': page_size,
            'unread_count': unread_count(user.pk)}


def mark_read(user, data):
    service.strict(data, ('ids',))
    identifiers = data.get('ids')
    if not isinstance(identifiers, list) or len(identifiers) > 100:
        raise ValueError('每次最多标记 100 项提醒')
    if any(type(identifier) is not int for identifier in identifiers):
        raise ValueError('提醒标识必须为正整数')
    for identifier in identifiers:
        service.positive(identifier)
    with transaction.atomic():
        actor = service.require_admin(user, lock=True)
        actual = set(ExpiryReminder.objects.filter(pk__in=identifiers).values_list('pk', flat=True))
        if actual != set(identifiers):
            raise ValueError('提醒不存在，请刷新后重试')
        for identifier in actual:
            ExpiryReminderRead.objects.get_or_create(user=actor, reminder_id=identifier)
    return {'ok': True}


@retry_lock_conflicts
def generate_reminders():
    with transaction.atomic():
        service.lock_decisions()
        # Only enabled canonical rows are candidates; linked panel metadata is paused.
        candidates = [row for row in assets.inventory().values() if row['reminder_enabled'] and not row['exempt']]
        locked = []
        for candidate in candidates:
            try:
                service.lock_asset(candidate['key'], resolve=False)
            except ValueError:
                continue
            locked.append(candidate)
        # Resolve once after acquiring the relevant source/association locks.
        current = assets.inventory()
        for candidate in locked:
            row = current.get(candidate['key'])
            if row is None:
                continue
            if row['version'] != candidate['version'] or not row['reminder_enabled'] or row['exempt']:
                continue
            stage = dates.reminder_stage(row['days_remaining'])
            if stage is None or row['expires_on'] is None:
                continue
            ExpiryReminder.objects.get_or_create(asset_key=row['key'], expires_on=row['expires_on'], stage=stage,
                                                 defaults={'name': row['name'][:100], 'address': row['address'][:255]})


def check_expiry():
    """Expose safe attempt/failure state without replacing prior successful check time."""
    try:
        ExpiryCheck.objects.update_or_create(pk=1, defaults={
            'status': 'running', 'last_attempt_at': timezone.now(), 'message': '正在检查本地到期记录'})
        generate_reminders()
        ExpiryCheck.objects.filter(pk=1).update(status='ok', last_success_at=timezone.now(),
                                               message='本地检查完成，仅生成管理员站内提醒')
        return True
    except Exception:
        try:
            ExpiryCheck.objects.filter(pk=1).update(status='error', message='本地检查失败，请核对数据库及调度服务')
        except Exception:
            pass  # An unavailable database cannot reliably persist its own failure.
        return False


def scheduled_check_expiry():
    try:
        check_expiry()
    finally:
        connections.close_all()
