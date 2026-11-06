# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django.db.models import F
from libs import json_response, JsonParser, Argument, auth
from apps.host.models import Group
from apps.account.models import Role
from apps.account import access
from apps.account.models import ResourceGroupOwner
from django.db import transaction


def fetch_children(data, with_hosts):
    if data:
        sub_data = dict()
        for item in Group.objects.filter(parent_id__in=data.keys()):
            tmp = item.to_view(with_hosts)
            sub_data[item.id] = tmp
            data[item.parent_id]['children'].append(tmp)
        return fetch_children(sub_data, with_hosts)


def merge_children(data, prefix, childes):
    prefix = f'{prefix}/' if prefix else ''
    for item in childes:
        name = f'{prefix}{item["title"]}'
        item['name'] = name
        if item.get('children'):
            merge_children(data, name, item['children'])
        else:
            data[item['key']] = name


def filter_by_perm(data, result, ids):
    for item in data:
        if 'children' in item:
            if item['key'] in ids:
                result.append(item)
            elif item['children']:
                filter_by_perm(item['children'], result, ids)


class GroupView(View):
    @auth('host.group.view|host.host.view|host.console.view|host.console.list|exec.task.do|exec.transfer.do|schedule.schedule.add|schedule.schedule.edit|monitor.monitor.add|monitor.monitor.edit|deploy.app.view|pipeline.pipeline.view')
    def get(self, request):
        with_hosts = request.GET.get('with_hosts')
        ids = access.group_ids(request.user, 'host')
        host_ids = access.allowed_ids(request.user, 'host')
        data, data2, parents = {}, {}, {}
        for item in Group.objects.filter(pk__in=ids):
            entry = item.to_view(False)
            if with_hosts:
                entry['children'] = [dict(title=h.name, hostname=h.hostname,
                    key=f'{item.id}_{h.id}', id=h.id, isLeaf=True)
                    for h in item.hosts.filter(pk__in=host_ids)]
            data[item.pk], parents[item.pk] = entry, item.parent_id
        if not data and request.user.is_supper:
            grp = Group.objects.create(name='Default', sort_id=1)
            data[grp.id] = grp.to_view()
        tree_data = []
        for pk, entry in data.items():
            if parents.get(pk) in data and parents.get(pk) != pk:
                data[parents[pk]]['children'].append(entry)
            else:
                tree_data.append(entry)
        merge_children(data2, '', tree_data)
        return json_response({'treeData': tree_data, 'groups': data2})

    @auth('host.group.add|host.group.edit')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('parent_id', type=int, default=0),
            Argument('name', help='请输入分组名称')
        ).parse(request.body)
        if error is None:
            access.require(request.user, 'host.group.edit' if form.id else 'host.group.add')
            if form.id:
                access.require_groups(request.user, 'host', [form.id], manage=True).update(name=form.name)
            else:
                if form.parent_id:
                    access.require_groups(request.user, 'host', [form.parent_id], manage=True)
                with transaction.atomic():
                    group = Group.objects.create(**form)
                    access.own_group(request.user, 'host', group.pk)
                    group.sort_id = group.id
                    group.save()
        return json_response(error=error)

    @auth('host.group.move')
    def patch(self, request):
        form, error = JsonParser(
            Argument('s_id', type=int, help='参数错误'),
            Argument('d_id', type=int, help='参数错误'),
            Argument('action', type=int, help='参数错误')
        ).parse(request.body)
        if error is None:
            access.require_groups(request.user, 'host', [form.s_id, form.d_id], manage=True)
            descendants, pending = {form.s_id}, {form.s_id}
            while pending:
                pending = set(Group.objects.filter(parent_id__in=pending).values_list('pk', flat=True)) - descendants
                descendants.update(pending)
            access.require_groups(request.user, 'host', list(descendants), manage=True)
            src, dst = Group.objects.get(pk=form.s_id), Group.objects.get(pk=form.d_id)
            cursor, visited = dst, {src.pk}
            while cursor:
                if cursor.pk in visited:
                    return json_response(error='不能移动分组到自身或子分组')
                visited.add(cursor.pk)
                cursor = Group.objects.filter(pk=cursor.parent_id).first()
            if not request.user.is_supper:
                parent_id = dst.pk if form.action == 0 else dst.parent_id
                if parent_id:
                    access.require_groups(request.user, 'host', [parent_id], manage=True)
                src.parent_id, src.sort_id = parent_id, dst.sort_id + (1 if form.action < 0 else -1)
                src.save()
                return json_response()
            if form.action == 0:
                src.parent_id = dst.id
                dst = Group.objects.filter(parent_id=dst.id).first()
                if not dst:
                    src.save()
                    return json_response()
                form.action = -1
            src.parent_id = dst.parent_id
            if src.sort_id > dst.sort_id:
                if form.action == -1:
                    dst = Group.objects.filter(sort_id__gt=dst.sort_id).last()
                Group.objects.filter(sort_id__lt=src.sort_id, sort_id__gte=dst.sort_id).update(sort_id=F('sort_id') + 1)
            else:
                if form.action == 1:
                    dst = Group.objects.filter(sort_id__lt=dst.sort_id).first()
                Group.objects.filter(sort_id__lte=dst.sort_id, sort_id__gt=src.sort_id).update(sort_id=F('sort_id') - 1)
            src.sort_id = dst.sort_id
            src.save()
        return json_response(error=error)

    @auth('host.group.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误')
        ).parse(request.GET)
        if error is None:
            access.require_groups(request.user, 'host', [form.id], manage=True)
            group = Group.objects.filter(pk=form.id).first()
            if not group:
                return json_response(error='未找到指定分组')
            if Group.objects.filter(parent_id=group.id).exists():
                return json_response(error='请移除子分组后再尝试删除')
            if group.hosts.exists():
                return json_response(error='请移除分组下的主机后再尝试删除')
            if not Group.objects.exclude(pk=form.id).exists():
                return json_response(error='请至少保留一个分组')
            # group_perms 是 JSONField，不能再用文本正则去匹配（MySQL 8 的 json 列上 REGEXP 行为不可靠），
            # 角色数量很少，直接在 Python 里判断即可
            for role in Role.objects.all():
                if str(form.id) in {str(x) for x in role.get_group_perms()}:
                    return json_response(error=f'账户角色【{role.name}】的主机权限关联该分组，请解除关联后再尝试删除')
            group.delete()
            ResourceGroupOwner.objects.filter(kind='host', group_id=form.id).delete()
        return json_response(error=error)
