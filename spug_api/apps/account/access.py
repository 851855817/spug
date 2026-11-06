"""Single authority for account resource access and serialized quota changes.

Callers must check both a feature permission and a resource scope. Linkage is an
identity hint for counting only: it never grants access to the other connection.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging

from django.db import transaction
from django.db.models import Q

from .models import (User, AccountResourcePolicy, ResourceOwnership, ResourceGrant,
                     ResourceGroupOwner, ResourceAccessAudit)


logger = logging.getLogger('spug.resource_access')


class AccessDenied(ValueError):
    """Safe public error with metadata that never contains request bodies."""


def reject(user, message, event='permission_denied', account_id=None, **detail):
    pk = getattr(user, 'pk', user if type(user) is int else None)
    account_id = pk if account_id is None else account_id
    # Logging is independent of database rollback, including nested worker
    # transactions. Successful policy changes also have transactional DB audit.
    logger.warning('Resource access rejected: %s', json.dumps(
        {'actor_id': pk, 'account_id': account_id, 'event': event,
         'at': datetime.now(timezone.utc).isoformat(), **detail}, ensure_ascii=True))
    if pk and User.objects.filter(pk=pk).exists():
        ResourceAccessAudit.objects.create(actor_id=pk, account_id=account_id, event=event, detail=detail)
    raise AccessDenied(message)


def current_user(user, lock=False):
    qs = User.objects.select_for_update() if lock else User.objects
    current = qs.filter(pk=getattr(user, 'pk', user if type(user) is int else None),
                        is_active=True, is_deleted=False).first()
    if current is None:
        reject(user, '账号已停用、删除或无权访问', event='account_denied')
    return current


def permissions(user):
    current = current_user(user)
    result = set()
    for role in current.roles.all():
        for module, pages in role.get_page_perms().items():
            if not isinstance(pages, dict):
                continue
            for page, actions in pages.items():
                if isinstance(actions, list):
                    result.update('%s.%s.%s' % (module, page, a) for a in actions if isinstance(a, str))
    return current, result


def require(user, *codes, lock=False):
    current = current_user(user, lock=lock)
    current, granted = permissions(current)
    if not current.is_supper and not set(codes).issubset(granted):
        reject(current, '权限拒绝', codes=list(codes))
    return current


def model(kind):
    if kind == 'host':
        from apps.host.models import Host
        return Host
    if kind == 'panel':
        from apps.baota.models import Panel
        return Panel
    raise ValueError('资源类型无效')


def scope(user, kind, manage=False):
    current = current_user(user)
    qs = model(kind).objects.all()
    if current.is_supper:
        return qs
    ownership = (Q(resource_ownership__owner_id=current.pk) |
                 Q(resource_ownership__isnull=True, created_by_id=current.pk))
    grants = ResourceGrant.objects.filter(user_id=current.pk)
    if manage:
        grants = grants.filter(level='manage')
    return qs.filter(ownership | Q(pk__in=grants.values(kind + '_id'))).distinct()


def allowed_ids(user, kind, manage=False):
    return set(scope(user, kind, manage).values_list('pk', flat=True))


def identifiers(ids):
    if not isinstance(ids, (list, tuple, set)) or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError('资源编号必须为正整数列表')
    return set(ids)


def require_resources(user, kind, ids, manage=False):
    selected = identifiers(ids)
    if not selected.issubset(allowed_ids(user, kind, manage)):
        reject(user, '资源不存在或无权访问', event='resource_denied',
               kind=kind, ids=sorted(selected), manage=bool(manage))
    return scope(user, kind, manage).filter(pk__in=selected)


def group_model(kind):
    if kind == 'host':
        from apps.host.models import Group
        return Group
    if kind == 'panel':
        from apps.baota.models import PanelGroup
        return PanelGroup
    raise ValueError('分组类型无效')


def group_ids(user, kind, manage=False):
    current = current_user(user)
    groups = dict(group_model(kind).objects.values_list('pk', 'parent_id'))
    if current.is_supper:
        return set(groups)
    result = set(ResourceGroupOwner.objects.filter(kind=kind, owner_id=current.pk).values_list('group_id', flat=True))
    if not manage:
        relation = 'groups__pk' if kind == 'host' else 'group_id'
        result.update(scope(current, kind).values_list(relation, flat=True))
        result.discard(None)
        pending = set(result)
        while pending:
            parents = {groups.get(pk) for pk in pending} - result - {None, 0}
            result.update(parents)
            pending = parents
    return result.intersection(groups)


def require_groups(user, kind, ids, manage=False):
    selected = identifiers(ids)
    if not selected.issubset(group_ids(user, kind, manage)):
        reject(user, '分组不存在或无权访问', event='group_denied',
               kind=kind, ids=sorted(selected), manage=bool(manage))
    return group_model(kind).objects.filter(pk__in=selected)


def own_group(user, kind, group_id):
    current = current_user(user)
    if not group_model(kind).objects.filter(pk=group_id).exists():
        raise ValueError('分组不存在')
    # Only creation may claim an unowned group; updates must call require_groups.
    return ResourceGroupOwner.objects.create(kind=kind, group_id=group_id, owner=current)


def quota_usage(user):
    current = current_user(user)
    hosts, panels = allowed_ids(current, 'host'), allowed_ids(current, 'panel')
    from apps.baota.resource_monitor.models import ResourceMonitorPreference
    links = dict(ResourceMonitorPreference.objects.filter(panel_id__in=panels, linked_host_id__isnull=False)
                 .values_list('panel_id', 'linked_host_id'))
    keys = {'host:%s' % pk for pk in hosts}
    keys.update('host:%s' % links[pk] if pk in links else 'panel:%s' % pk for pk in panels)
    policy = AccountResourcePolicy.objects.filter(user_id=current.pk).first()
    limit = None if current.is_supper else policy.server_limit if policy else 0
    used = len(keys)
    return {'used': used, 'limit': limit, 'remaining': None if limit is None else max(0, limit - used)}


@contextmanager
def quota_guard(user_ids, actor=None):
    # Lock the durable User row, including accounts without a policy record.
    # All resource additions/assignments/transfers/link changes use this order.
    selected = sorted(identifiers(list(user_ids)))
    denied = None
    try:
        with transaction.atomic():
            users = list(User.objects.select_for_update().filter(pk__in=selected).order_by('pk'))
            if len(users) != len(selected):
                raise ValueError('账号不存在')
            before = {u.pk: quota_usage(u)['used'] for u in users if u.is_active and not u.is_deleted}
            yield
            for u in users:
                if u.pk not in before:
                    continue
                usage = quota_usage(u)
                if usage['limit'] is not None and usage['used'] > usage['limit'] and usage['used'] > before[u.pk]:
                    denied = (u, before[u.pk], usage['limit'])
                    raise AccessDenied('当前已管理 %s / %s 台服务器，无法增加；请联系管理员调整额度。' %
                                       (before[u.pk], usage['limit']))
    except AccessDenied as exc:
        if denied:
            user, used, limit = denied
            reject(actor or user, str(exc), event='quota_denied', account_id=user.pk, used=used, limit=limit)
        raise


def admin(user):
    current = current_user(user)
    if not current.is_supper:
        reject(current, '仅管理员可设置资源授权与配额')
    return current


def audit(actor, account, event, **detail):
    ResourceAccessAudit.objects.create(actor=actor, account=account, event=event, detail=detail)


def set_policy(actor, user, limit):
    actor, user = admin(actor), current_user(user)
    if limit is not None and (type(limit) is not int or limit < 0 or limit > 2147483647):
        raise ValueError('服务器上限必须为非负整数或显式不限')
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        AccountResourcePolicy.objects.update_or_create(user=user, defaults={'server_limit': limit})
        audit(actor, user, 'quota_changed', limit=limit)
    return quota_usage(user)


def assign(actor, user, kind, ids, level):
    actor, user = admin(actor), current_user(user)
    selected = identifiers(ids)
    if level not in ('read', 'manage', 'none'):
        raise ValueError('资源授权级别无效')
    require_resources(actor, kind, list(selected))
    with quota_guard([user.pk], actor=actor):
        for pk in sorted(selected):
            lookup = {'user': user, kind + '_id': pk}
            if level == 'none':
                ResourceGrant.objects.filter(**lookup).delete()
            else:
                ResourceGrant.objects.update_or_create(**lookup, defaults={'level': level})
        audit(actor, user, 'resource_grant', kind=kind, ids=sorted(selected), level=level)


def affected_accounts(kind, ids):
    selected = identifiers(ids)
    qs = model(kind).objects.filter(pk__in=selected)
    creators = set(qs.filter(resource_ownership__isnull=True).values_list('created_by_id', flat=True))
    owners = set(ResourceOwnership.objects.filter(**{kind + '_id__in': selected}).values_list('owner_id', flat=True))
    grants = set(ResourceGrant.objects.filter(**{kind + '_id__in': selected}).values_list('user_id', flat=True))
    affected = creators | owners | grants
    if kind == 'host':
        # Deleting a host SET_NULLs links and may split several panel records
        # counted as one server by accounts that cannot read the SSH host.
        from apps.baota.resource_monitor.models import ResourceMonitorPreference
        panels = list(ResourceMonitorPreference.objects.filter(linked_host_id__in=selected)
                      .values_list('panel_id', flat=True))
        if panels:
            affected.update(affected_accounts('panel', [pk for pk in panels if pk is not None]))
    return affected


def transfer(actor, kind, resource_id, user):
    actor, user = admin(actor), current_user(user)
    require_resources(actor, kind, [resource_id])
    affected = affected_accounts(kind, [resource_id]) | {user.pk}
    with quota_guard(affected, actor=actor):
        ResourceOwnership.objects.update_or_create(**{kind + '_id': resource_id}, defaults={'owner': user})
        audit(actor, user, 'resource_transfer', kind=kind, id=resource_id)


def require_source(user, key, manage=False):
    if not isinstance(key, str) or ':' not in key:
        raise ValueError('资源来源无效')
    kind, value = key.split(':', 1)
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise ValueError('资源来源无效')
    require_resources(user, kind, [int(value)], manage=manage)
    return kind, int(value)
