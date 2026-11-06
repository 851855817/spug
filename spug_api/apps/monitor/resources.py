"""Legacy detections keep their owner and full target boundary at every entry."""
import json
from apps.account import access
from .models import Detection


def host_ids(record):
    if record.type not in ('3', '4'):
        return set()
    try:
        values = json.loads(record.targets)
        if not isinstance(values, list) or not values or any(isinstance(pk, bool) for pk in values):
            raise ValueError()
        return access.identifiers([int(pk) for pk in values])
    except (ValueError, TypeError):
        raise ValueError('监控主机配置无效') from None


def authorize(user, record, manage=False, code=None):
    current = access.require(user, code) if code else access.current_user(user)
    if not current.is_supper and record.created_by_id != current.pk:
        access.reject(current, '监控项不存在或无权访问')
    access.require_resources(current, 'host', list(host_ids(record)), manage=manage)
    if manage and record.type == '4':
        access.require(current, 'exec.task.do')
    if manage and hasattr(record, 'notify_grp'):
        from apps.alarm.models import Group, Contact
        from apps.alarm.resources import require_owned
        groups = json.loads(record.notify_grp)
        require_owned(current, Group, groups)
        contacts = [pk for group in Group.objects.filter(pk__in=groups) for pk in json.loads(group.contacts)]
        require_owned(current, Contact, contacts)
    return current


def visible(user):
    current = access.current_user(user)
    qs = Detection.objects.all()
    if current.is_supper:
        return qs
    allowed, visible_ids = access.allowed_ids(current, 'host'), []
    for record in qs.filter(created_by_id=current.pk):
        try:
            if host_ids(record).issubset(allowed):
                visible_ids.append(record.pk)
        except ValueError:
            continue
    return qs.filter(pk__in=visible_ids)


def runtime(user, record):
    """Creating an enabled check and editing it can both authorize its runtime."""
    current, granted = access.permissions(user)
    if not current.is_supper and not {'monitor.monitor.add', 'monitor.monitor.edit'}.intersection(granted):
        access.reject(current, '监控执行权限已撤销')
    return authorize(current, record, manage=True)
