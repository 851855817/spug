import json
from apps.account import access
from apps.app.models import App
from apps.schedule.models import Task


def apps(user):
    current = access.current_user(user)
    qs = App.objects.all()
    return qs if current.is_supper else qs.filter(pk__in=current.deploy_perms['apps'])


def tasks(user):
    from apps.schedule.resources import visible
    try:
        return visible(user)
    except access.AccessDenied:
        return Task.objects.none()
