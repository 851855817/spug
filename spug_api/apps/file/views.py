# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django_redis import get_redis_connection
from apps.host.models import Host
from apps.account.utils import has_host_perm
from apps.account.utils import register_resource_stream
from apps.account import access
from apps.file.utils import FileResponseAfter, fetch_dir_list
from libs import json_response, JsonParser, Argument, auth
from functools import partial
import os
import uuid
import json


def _authorize_file(user, host_id, permission, manage=False):
    access.require(user, permission)
    access.require_resources(user, 'host', [host_id], manage=manage)


class FileView(View):
    @auth('host.console.list')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('path', help='参数错误')
        ).parse(request.GET)
        if error is None:
            if not has_host_perm(request.user, form.id):
                return json_response(error='无权访问主机，请联系管理员')
            host = Host.objects.get(pk=form.id)
            if not host:
                return json_response(error='未找到指定主机')
            objects = fetch_dir_list(host, form.path, user=request.user)
            access.require(request.user, 'host.console.list')
            access.require_resources(request.user, 'host', [form.id])
            return json_response(objects)
        return json_response(error=error)


class ObjectView(View):
    @auth('host.console.upload')
    def put(self, request):
        form, error = JsonParser(Argument('id', type=int, help='参数错误')).parse(request.body)
        if error is None:
            access.require_resources(request.user, 'host', [form.id], manage=True)
            token = uuid.uuid4().hex
            register_resource_stream(token, request.user, [form.id], 'host.console.upload',
                                     rds=get_redis_connection())
            return json_response(token)
        return json_response(error=error)

    @auth('host.console.list')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('file', help='请输入文件路径')
        ).parse(request.GET)
        if error is None:
            if not has_host_perm(request.user, form.id):
                return json_response(error='无权访问主机，请联系管理员')
            host = Host.objects.filter(pk=form.id).first()
            if not host:
                return json_response(error='未找到指定主机')
            filename = os.path.basename(form.file)
            authorize = partial(_authorize_file, request.user, form.id, 'host.console.list')
            ssh_cli = host.get_ssh(authorize=authorize).get_client()
            try:
                access.require(request.user, 'host.console.list')
                access.require_resources(request.user, 'host', [form.id])
                sftp = ssh_cli.open_sftp()
                access.require(request.user, 'host.console.list')
                access.require_resources(request.user, 'host', [form.id])
                f = sftp.open(form.file)
            except Exception:
                ssh_cli.close()
                raise
            return FileResponseAfter(ssh_cli.close, f, as_attachment=True, filename=filename,
                                     user=request.user, host_id=form.id)
        return json_response(error=error)

    @auth('host.console.upload')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('token', help='参数错误'),
            Argument('path', help='参数错误'),
        ).parse(request.POST)
        if error is None:
            if not has_host_perm(request.user, form.id, manage=True):
                return json_response(error='无权访问主机，请联系管理员')
            file = request.FILES.get('file')
            if not file:
                return json_response(error='请选择要上传的文件')
            host = Host.objects.get(pk=form.id)
            if not host:
                return json_response(error='未找到指定主机')
            rds_cli = get_redis_connection()
            raw = rds_cli.get('resource-stream:' + form.token)
            try:
                meta = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                meta = {}
            if (meta.get('owner_id') != request.user.pk or meta.get('host_ids') != [form.id]
                    or meta.get('code') != 'host.console.upload'):
                access.reject(request.user, '上传票据不存在或无权使用')
            callback = partial(self._compute_progress, rds_cli, form.token, file.size, request.user, form.id)
            access.require(request.user, 'host.console.upload')
            access.require_resources(request.user, 'host', [form.id], manage=True)
            authorize = partial(_authorize_file, request.user, form.id, 'host.console.upload', True)
            with host.get_ssh(authorize=authorize) as ssh:
                access.require(request.user, 'host.console.upload')
                access.require_resources(request.user, 'host', [form.id], manage=True)
                ssh.put_file_by_fl(file, f'{form.path}/{file.name}', callback=callback)
        return json_response(error=error)

    @auth('host.console.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('file', help='请输入文件路径')
        ).parse(request.GET)
        if error is None:
            if not has_host_perm(request.user, form.id, manage=True):
                return json_response(error='无权访问主机，请联系管理员')
            host = Host.objects.get(pk=form.id)
            if not host:
                return json_response(error='未找到指定主机')
            authorize = partial(_authorize_file, request.user, form.id, 'host.console.del', True)
            with host.get_ssh(authorize=authorize) as ssh:
                access.require(request.user, 'host.console.del')
                access.require_resources(request.user, 'host', [form.id], manage=True)
                ssh.remove_file(form.file)
        return json_response(error=error)

    def _compute_progress(self, rds_cli, token, total, user, host_id, value, *args):
        access.require(user, 'host.console.upload')
        access.require_resources(user, 'host', [host_id], manage=True)
        percent = '%.1f' % (value / max(total, 1) * 100)
        rds_cli.publish(token, percent)
