"""Account policy UI endpoints; resource records never serialize credentials."""
import json
from django.views import View
from libs import json_response
from . import access
from .models import User, ResourceGrant


def account(value):
    if type(value) is not int or value <= 0:
        raise ValueError('账号编号无效')
    return access.current_user(value)


def resource_row(resource, kind, level):
    return {'id': resource.pk, 'name': resource.name, 'level': level, 'kind': kind}


def effective(user, administrator=False):
    current, perms = access.permissions(user)
    result = {'user_id': current.pk, 'quota': access.quota_usage(current),
              'is_supper': current.is_supper, 'permissions': sorted(perms), 'grants': []}
    grants = list(ResourceGrant.objects.filter(user_id=current.pk).order_by('pk'))
    for g in grants:
        result['grants'].append({'kind': 'host' if g.host_id else 'panel', 'id': g.host_id or g.panel_id,
                                 'level': g.level})
    for kind, plural in (('host', 'hosts'), ('panel', 'panels')):
        managed = access.allowed_ids(current, kind, manage=True)
        result[plural] = [dict(resource_row(obj, kind, 'manage' if obj.pk in managed else 'read'),
                              source='owned' if access.owner_id(kind, obj) == current.pk else 'assigned')
                          for obj in access.scope(current, kind).order_by('pk')]
        if administrator:
            result['available_' + plural] = [resource_row(obj, kind, '')
                                             for obj in access.model(kind).objects.order_by('pk')]
    return result


def update_policy(actor, user, data):
    access.admin(actor)
    if set(data) - {'user_id', 'limit', 'grants'}:
        raise ValueError('不支持的资源策略字段')
    if 'limit' not in data and 'grants' not in data:
        raise ValueError('请提供配额或资源授权')
    grants = data.get('grants')
    if grants is not None:
        if not isinstance(grants, list) or len(grants) > 10000:
            raise ValueError('资源授权列表无效')
        seen = set()
        for row in grants:
            if not isinstance(row, dict) or set(row) != {'kind', 'id', 'level'}:
                raise ValueError('资源授权字段无效')
            if row['level'] not in ('read', 'manage'):
                raise ValueError('资源授权级别无效')
            access.model(row['kind'])
            access.identifiers([row['id']])
            identity = (row['kind'], row['id'])
            if identity in seen:
                raise ValueError('资源授权不能重复')
            seen.add(identity)
    # User row locks serialize edits with concurrent additions. quota_guard
    # encloses both replacement and limit changes, so failure rolls both back.
    with access.quota_guard([user.pk], actor=actor):
        actor, user = access.admin(actor), access.current_user(user)
        if 'limit' in data:
            access.set_policy(actor, user, data['limit'])
        if grants is not None:
            previous = [{'kind': 'host' if g.host_id else 'panel', 'id': g.host_id or g.panel_id, 'level': g.level}
                        for g in ResourceGrant.objects.filter(user_id=user.pk).order_by('pk')]
            for row in grants:
                access.require_resources(actor, row['kind'], [row['id']])
            ResourceGrant.objects.filter(user_id=user.pk).delete()
            ResourceGrant.objects.bulk_create([ResourceGrant(user=user, level=row['level'],
                **{row['kind'] + '_id': row['id']}) for row in grants])
            access.audit(actor, user, 'resource_grants_replaced', before=previous, after=grants)
    return effective(user, administrator=True)


class ResourceView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            access.current_user(request.user)
            response = super().dispatch(request, *args, **kwargs)
        except ValueError as exc:
            response = json_response(error=str(exc))
        response['Cache-Control'] = 'no-store, private'
        return response

    def get(self, request):
        user = request.user
        if request.GET.get('user_id'):
            try:
                selected = int(request.GET['user_id'])
            except (TypeError, ValueError):
                raise ValueError('账号编号无效') from None
            if selected != user.pk:
                access.admin(user)
            user = account(selected)
        return json_response(effective(user, administrator=access.current_user(request.user).is_supper))

    def post(self, request):
        access.admin(request.user)
        if len(request.body) > 1024 * 1024:
            raise ValueError('授权数据过大')
        try:
            data = json.loads(request.body)
        except (ValueError, TypeError):
            raise ValueError('授权数据格式无效') from None
        if not isinstance(data, dict):
            raise ValueError('授权数据格式无效')
        return json_response(update_policy(request.user, account(data.get('user_id')), data))

    def patch(self, request):
        access.admin(request.user)
        try:
            data = json.loads(request.body)
        except (ValueError, TypeError):
            raise ValueError('归属数据格式无效') from None
        if not isinstance(data, dict) or set(data) != {'kind', 'id', 'owner_id'}:
            raise ValueError('归属数据字段无效')
        access.transfer(request.user, data['kind'], data['id'], account(data['owner_id']))
        return json_response()
