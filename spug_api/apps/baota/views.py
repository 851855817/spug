from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.views import View

from libs import auth, json_response
from apps.account import access
from apps.account.models import ResourceGroupOwner
from .authorization import panel_view
from .models import Panel, PanelGroup
from .security import encrypt_secret, SecretError
from .validation import api_configuration, parse_body, positive_id, text, panel_url


class PanelView(View):
    @auth('baota.panel.view')
    def get(self, request):
        return json_response([panel_view(request.user, panel) for panel in access.scope(request.user, 'panel')])

    def post(self, request):
        try:
            data = parse_body(request)
            record_id = positive_id(data.get('id'), required=False)
            permission = 'baota.panel.edit' if record_id else 'baota.panel.add'
            user = access.require(request.user, permission)
            if record_id:
                access.require_resources(user, 'panel', [record_id], manage=True)
            values = {
                'name': text(data, 'name', '面板名称', 100),
                'url': panel_url(text(data, 'url', '面板地址', 2048)),
                'username': text(data, 'username', '登录账户', 100),
                'desc': text(data, 'desc', '备注', 500, required=False),
            }
            group_id = positive_id(data.get('group_id'), required=not record_id)
            password = text(data, 'password', '登录密码', 4096, required=not record_id, strip=False)
            if password:
                values['password_ciphertext'] = encrypt_secret(password)
            affected = lambda: access.affected_accounts('panel', [record_id]) if record_id else {user.pk}
            with access.quota_guard(affected, actor=user):
                panel = (Panel.objects.select_for_update().get(pk=record_id) if record_id
                         else Panel(created_by=user))
                group_id = panel.group_id if group_id is None else group_id
                group = PanelGroup.objects.select_for_update().get(pk=group_id)
                values['group'] = group
                user = access.require(user.pk, permission)
                if record_id:
                    access.require_resources(user, 'panel', [record_id], manage=True)
                if not record_id or group_id != panel.group_id:
                    if record_id and not user.is_supper and access.owner_id('panel', panel) != user.pk:
                        access.reject(user, '仅资源归属账号或管理员可移动面板分组')
                    access.require_groups(user, 'panel', [group_id], manage=True)
                api_values, api_key, clear_api_key = api_configuration(data, panel)
                if api_key:
                    api_values['api_key_ciphertext'] = encrypt_secret(api_key)
                elif clear_api_key:
                    api_values['api_key_ciphertext'] = ''
                if any(getattr(panel, key) != value for key, value in api_values.items()):
                    panel.api_config_revision += 1
                    panel.api_checked_at, panel.api_version = '', ''
                values.update(api_values)
                for key, value in values.items():
                    setattr(panel, key, value)
                access.require(user.pk, permission)
                if record_id:
                    access.require_resources(user, 'panel', [record_id], manage=True)
                panel.save()
            return json_response(panel_view(user, panel))
        except (ValueError, SecretError) as exc:
            return json_response(error=str(exc))
        except (Panel.DoesNotExist, PanelGroup.DoesNotExist):
            return json_response(error='面板或分组不存在，请刷新列表后重试')

    @auth('baota.panel.del')
    def delete(self, request):
        try:
            record_id = positive_id(request.GET.get('id'))
            access.require_resources(request.user, 'panel', [record_id], manage=True)
            with access.quota_guard(lambda: access.affected_accounts('panel', [record_id]), actor=request.user):
                panel = Panel.objects.select_for_update().get(pk=record_id)
                access.require(request.user.pk, 'baota.panel.del')
                access.require_resources(request.user.pk, 'panel', [record_id], manage=True)
                panel.delete()
            return json_response()
        except ValueError as exc:
            return json_response(error=str(exc))
        except Panel.DoesNotExist:
            return json_response(error='面板不存在，请刷新列表后重试')
        except ProtectedError:
            return json_response(error='面板存在任务记录或保留锁，不能删除')


class GroupView(View):
    @auth('baota.panel.view|baota.group.manage')
    def get(self, request):
        managed_ids = access.group_ids(request.user, 'panel', manage=True)
        return json_response([dict(group.to_view(), can_manage=group.pk in managed_ids)
            for group in PanelGroup.objects.filter(pk__in=access.group_ids(request.user, 'panel'))])

    @auth('baota.group.manage')
    def post(self, request):
        try:
            data = parse_body(request)
            record_id = positive_id(data.get('id'), required=False)
            name = text(data, 'name', '分组名称', 50)
            parent_id = positive_id(data.get('parent_id'), required=False)
            if record_id:
                access.require_groups(request.user, 'panel', [record_id], manage=True)
            if parent_id:
                access.require_groups(request.user, 'panel', [parent_id], manage=True)
            with transaction.atomic():
                # Group updates are small; lock the tree to prevent concurrent cycles.
                groups = {group.id: group for group in PanelGroup.objects.select_for_update()}
                group = groups.get(record_id) if record_id else PanelGroup()
                if group is None or (parent_id and parent_id not in groups):
                    return json_response(error='分组不存在，请刷新列表后重试')
                ancestor_id = parent_id
                visited = set()
                while ancestor_id:
                    if ancestor_id == record_id or ancestor_id in visited:
                        return json_response(error='不能将分组移到自身或其子分组下')
                    visited.add(ancestor_id)
                    ancestor_id = groups[ancestor_id].parent_id
                if PanelGroup.objects.filter(parent_id=parent_id, name=name).exclude(pk=record_id).exists():
                    return json_response(error='同一层级已存在该名称的分组')
                group.name, group.parent_id = name, parent_id
                group.save()
                if not record_id:
                    access.own_group(request.user, 'panel', group.pk)
            return json_response(group.to_view())
        except ValueError as exc:
            return json_response(error=str(exc))
        except IntegrityError:
            return json_response(error='分组保存失败，请刷新后重试')

    @auth('baota.group.manage')
    def delete(self, request):
        try:
            record_id = positive_id(request.GET.get('id'))
            access.require_groups(request.user, 'panel', [record_id], manage=True)
            with transaction.atomic():
                group = PanelGroup.objects.select_for_update().get(pk=record_id)
                if group.panels.exists() or group.children.exists():
                    return json_response(error='分组包含面板或子分组，请先移动面板并处理子分组')
                group.delete()
                ResourceGroupOwner.objects.filter(kind='panel', group_id=record_id).delete()
            return json_response()
        except ValueError as exc:
            return json_response(error=str(exc))
        except PanelGroup.DoesNotExist:
            return json_response(error='分组不存在，请刷新列表后重试')
        except ProtectedError:
            return json_response(error='分组仍被使用，请刷新后重试')
