from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.views import View

from libs import auth, json_response
from .models import Panel, PanelGroup
from .security import encrypt_secret, SecretError
from .validation import api_configuration, parse_body, positive_id, text, panel_url


class PanelView(View):
    @auth('baota.panel.view')
    def get(self, request):
        return json_response([panel.to_view() for panel in Panel.objects.all()])

    def post(self, request):
        try:
            data = parse_body(request)
            record_id = positive_id(data.get('id'), required=False)
            permission = 'baota.panel.edit' if record_id else 'baota.panel.add'
            if not request.user.has_perms([permission]):
                return json_response(error='权限拒绝')
            values = {
                'name': text(data, 'name', '面板名称', 100),
                'url': panel_url(text(data, 'url', '面板地址', 2048)),
                'username': text(data, 'username', '登录账户', 100),
                'desc': text(data, 'desc', '备注', 500, required=False),
            }
            group_id = positive_id(data.get('group_id'))
            password = text(data, 'password', '登录密码', 4096, required=not record_id, strip=False)
            if password:
                values['password_ciphertext'] = encrypt_secret(password)
            with transaction.atomic():
                group = PanelGroup.objects.select_for_update().get(pk=group_id)
                values['group'] = group
                panel = (Panel.objects.select_for_update().get(pk=record_id) if record_id
                         else Panel(created_by=request.user))
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
                panel.save()
            return json_response(panel.to_view())
        except (ValueError, SecretError) as exc:
            return json_response(error=str(exc))
        except (Panel.DoesNotExist, PanelGroup.DoesNotExist):
            return json_response(error='面板或分组不存在，请刷新列表后重试')

    @auth('baota.panel.del')
    def delete(self, request):
        try:
            record_id = positive_id(request.GET.get('id'))
            panel = Panel.objects.get(pk=record_id)
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
        return json_response([group.to_view() for group in PanelGroup.objects.all()])

    @auth('baota.group.manage')
    def post(self, request):
        try:
            data = parse_body(request)
            record_id = positive_id(data.get('id'), required=False)
            name = text(data, 'name', '分组名称', 50)
            parent_id = positive_id(data.get('parent_id'), required=False)
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
            return json_response(group.to_view())
        except ValueError as exc:
            return json_response(error=str(exc))
        except IntegrityError:
            return json_response(error='分组保存失败，请刷新后重试')

    @auth('baota.group.manage')
    def delete(self, request):
        try:
            record_id = positive_id(request.GET.get('id'))
            with transaction.atomic():
                group = PanelGroup.objects.select_for_update().get(pk=record_id)
                if group.panels.exists() or group.children.exists():
                    return json_response(error='分组包含面板或子分组，请先移动面板并处理子分组')
                group.delete()
            return json_response()
        except ValueError as exc:
            return json_response(error=str(exc))
        except PanelGroup.DoesNotExist:
            return json_response(error='分组不存在，请刷新列表后重试')
        except ProtectedError:
            return json_response(error='分组仍被使用，请刷新后重试')
