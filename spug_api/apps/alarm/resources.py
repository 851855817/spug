from apps.account import access
from .models import Alarm


def owned(user, queryset):
    current = access.current_user(user)
    return queryset if current.is_supper else queryset.filter(created_by_id=current.pk)


def require_owned(user, model, ids):
    selected = access.identifiers(ids)
    if set(owned(user, model.objects.filter(pk__in=selected)).values_list('pk', flat=True)) != selected:
        access.reject(user, '联系对象不存在或无权访问')


def visible(user):
    user = access.current_user(user)
    qs = Alarm.objects.all()
    if user.is_supper:
        return qs
    hosts, ids = access.allowed_ids(user, 'host'), []
    for alarm in qs.filter(resource_scope__owner_id=user.pk).select_related('resource_scope'):
        try:
            if access.identifiers(alarm.resource_scope.host_ids).issubset(hosts):
                ids.append(alarm.pk)
        except ValueError:
            continue
    return qs.filter(pk__in=ids)
