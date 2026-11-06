# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django.db.models import F
from django.http.response import HttpResponseBadRequest, HttpResponse
from libs import json_response, JsonParser, Argument, AttrDict, auth
from apps.setting.utils import AppSetting
from apps.account.utils import register_resource_stream
from apps.account import access
from apps.host.models import Host, Group
from apps.host.utils import batch_sync_host, _sync_host_extend
from apps.app.models import Deploy
from apps.schedule.models import Task
from apps.monitor.models import Detection
from apps.exec.models import ExecTemplate
from libs.ssh import SSH, AuthenticationException
from libs.locale import translate, translate_choice
from paramiko.ssh_exception import BadAuthenticationType
from openpyxl import load_workbook, Workbook
from threading import Thread
import socket
import uuid
import json


class HostView(View):
    @auth('host.host.view|host.console.view|host.console.list|exec.task.do|exec.transfer.do|schedule.schedule.add|schedule.schedule.edit|monitor.monitor.add|monitor.monitor.edit|deploy.app.view|pipeline.pipeline.view')
    def get(self, request):
        hosts = access.scope(request.user, 'host').select_related('hostextend')
        hosts = {x.id: x.to_view() for x in hosts}
        for rel in Group.hosts.through.objects.filter(host_id__in=hosts.keys(),
                                                     group_id__in=access.group_ids(request.user, 'host')):
            hosts[rel.host_id]['group_ids'].append(rel.group_id)
        return json_response(list(hosts.values()))

    @auth('host.host.add|host.host.edit')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('group_ids', type=list, default=[], help='请选择主机分组'),
            Argument('name', help='请输主机名称'),
            Argument('username', handler=str.strip, help='请输入登录用户名'),
            Argument('hostname', handler=str.strip, help='请输入主机名或IP'),
            Argument('port', type=int, help='请输入SSH端口'),
            Argument('pkey', required=False),
            Argument('keep_pkey', type=bool, default=False),
            Argument('desc', required=False),
            Argument('password', required=False),
        ).parse(request.body)
        if error is None:
            request.user = access.require(request.user, 'host.host.edit' if form.id else 'host.host.add')
            access.identifiers(form.group_ids)
            existing = None
            if form.id:
                existing = access.require_resources(request.user, 'host', [form.id], manage=True).get()
            def authorize_groups(host, selected_groups):
                if host is None:
                    if not selected_groups:
                        raise ValueError('请选择主机分组')
                    access.require_groups(request.user, 'host', selected_groups, manage=True)
                elif not request.user.is_supper and access.owner_id('host', host) != request.user.pk:
                    current_groups = set(host.groups.values_list('pk', flat=True))
                    visible_groups = current_groups.intersection(access.group_ids(request.user, 'host'))
                    if selected_groups and set(selected_groups) != visible_groups:
                        access.reject(request.user, '仅资源所有者或管理员可修改主机分组')
                elif selected_groups:
                    access.require_groups(request.user, 'host', selected_groups, manage=True)
            authorize_groups(existing, form.group_ids)
            # 前端拿不到已保存的私钥，编辑时用 keep_pkey 表示沿用原独立密钥；不传 pkey 且不保留则清除
            keep_pkey = form.pop('keep_pkey')
            if form.id and keep_pkey and not form.pkey:
                form.pkey = existing.pkey
            same_endpoint = existing is not None and all(
                getattr(existing, field) == form[field] for field in ('hostname', 'port', 'username'))
            legacy_global = bool(existing and existing.created_by.is_supper and not existing.pkey and same_endpoint)
            # Ordinary managers may retain a legacy global connection at its
            # existing endpoint. An ordinary actor using a password installs an independent key.
            allow_global = bool(request.user.is_supper and (existing is None or existing.created_by.is_supper)
                                or legacy_global and not form.password)
            uses_global = allow_global and not form.pkey
            if not allow_global and not form.pkey and not form.password:
                return json_response(error='请提供独立 SSH 密钥或登录密码')
            selected_groups = form.group_ids
            def authorize_verify():
                request.user = access.require(request.user, 'host.host.edit' if form.id else 'host.host.add')
                if uses_global and not request.user.is_supper and not legacy_global:
                    access.reject(request.user, '请提供独立 SSH 密钥或登录密码')
                authorize_groups(existing, selected_groups)
                if form.id:
                    access.require_resources(request.user, 'host', [form.id], manage=True)
            if not _do_host_verify(form, allow_global=allow_global, authorize=authorize_verify):
                return json_response('auth fail')
            form.pop('password', None)

            group_ids = form.pop('group_ids')
            other = Host.objects.filter(name=form.name).first()
            if other and (not form.id or other.id != form.id):
                return json_response(error=f'已存在的主机名称【{form.name}】')
            with access.quota_guard([request.user.pk], actor=request.user):
                authorize_verify()
                if form.id:
                    host = access.require_resources(request.user, 'host', [form.id], manage=True).get()
                    authorize_groups(host, group_ids)
                    Host.objects.filter(pk=host.pk).update(is_verified=True, **form)
                    host.refresh_from_db()
                else:
                    access.require_groups(request.user, 'host', group_ids, manage=True)
                    host = Host.objects.create(created_by=request.user, is_verified=True, **form)
                if group_ids and (request.user.is_supper or access.owner_id('host', host) == request.user.pk):
                    preserved = list(host.groups.exclude(pk__in=access.group_ids(
                        request.user, 'host', manage=True)).values_list('pk', flat=True))
                    host.groups.set(preserved + group_ids)
            response = host.to_view()
            response['group_ids'] = list(host.groups.filter(pk__in=access.group_ids(
                request.user, 'host')).values_list('pk', flat=True))
            return json_response(response)
        return json_response(error=error)

    @auth('host.host.verify')
    def put(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误')
        ).parse(request.body)
        if error is None:
            host = access.require_resources(request.user, 'host', [form.id], manage=True).get()
            def authorize():
                access.require(request.user, 'host.host.verify')
                access.require_resources(request.user, 'host', [form.id], manage=True)
            with host.get_ssh(authorize=authorize) as ssh:
                authorize()
                _sync_host_extend(host, ssh=ssh, authorize=authorize)
        return json_response(error=error)

    @auth('host.group.move')
    def patch(self, request):
        form, error = JsonParser(
            Argument('host_ids', type=list, filter=lambda x: len(x), help='请选择主机'),
            Argument('s_group_id', type=int, help='参数错误'),
            Argument('t_group_id', type=int, help='参数错误'),
            Argument('is_copy', type=bool, help='参数错误'),
        ).parse(request.body)
        if error is None:
            hosts = access.require_resources(request.user, 'host', form.host_ids, manage=True)
            if not request.user.is_supper and any(access.owner_id('host', h) != request.user.pk for h in hosts):
                access.reject(request.user, '仅资源所有者或管理员可修改主机分组')
            access.require_groups(request.user, 'host', [form.s_group_id, form.t_group_id], manage=True)
            if form.t_group_id == form.s_group_id:
                return json_response(error='不能选择本分组的主机')
            s_group = Group.objects.get(pk=form.s_group_id)
            t_group = Group.objects.get(pk=form.t_group_id)
            t_group.hosts.add(*form.host_ids)
            if not form.is_copy:
                s_group.hosts.remove(*form.host_ids)
        return json_response(error=error)

    @auth('host.host.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('group_id', type=int, required=False),
        ).parse(request.GET)
        if error is None:
            if form.id:
                host_ids = [form.id]
            elif form.group_id:
                group = access.require_groups(request.user, 'host', [form.group_id], manage=True).get()
                host_ids = [x.id for x in group.hosts.all()]
            else:
                return json_response(error='参数错误')
            access.require_resources(request.user, 'host', host_ids, manage=True)
            for host_id in host_ids:
                regex = fr'[^0-9]{host_id}[^0-9]'
                deploy = Deploy.objects.filter(host_ids__regex=regex) \
                    .annotate(app_name=F('app__name'), env_name=F('env__name')).first()
                if deploy:
                    return json_response(
                        error=f'应用【{deploy.app_name}】在【{deploy.env_name}】的发布配置关联了该主机，请解除关联后再尝试删除该主机')
                task = Task.objects.filter(targets__regex=regex).first()
                if task:
                    return json_response(
                        error=f'任务计划中的任务【{task.name}】关联了该主机，请解除关联后再尝试删除该主机')
                detection = Detection.objects.filter(type__in=('3', '4'), targets__regex=regex).first()
                if detection:
                    return json_response(
                        error=f'监控中心的任务【{detection.name}】关联了该主机，请解除关联后再尝试删除该主机')
                tpl = ExecTemplate.objects.filter(host_ids__regex=regex).first()
                if tpl:
                    return json_response(error=f'执行模板【{tpl.name}】关联了该主机，请解除关联后再尝试删除该主机')
            with access.quota_guard(access.affected_accounts('host', host_ids), actor=request.user):
                access.require(request.user, 'host.host.del')
                access.require_resources(request.user, 'host', host_ids, manage=True)
                Host.objects.filter(pk__in=host_ids).delete()
        return json_response(error=error)


@auth('host.host.import')
def post_import(request):
    group_id = int(request.POST.get('group_id', 0))
    access.require_groups(request.user, 'host', [group_id], manage=True)
    file = request.FILES['file']
    hosts = []
    ws = load_workbook(file, read_only=True)['Sheet1']
    summary = {'fail': 0, 'success': 0, 'invalid': [], 'skip': [], 'repeat': []}
    candidates, seen_names, seen_targets = [], set(), set()
    for i, row in enumerate(ws.rows, start=1):
        if i == 1:  # 第1行是表头 略过
            continue
        if not all([row[x].value for x in range(4)]):
            summary['invalid'].append(i)
            summary['fail'] += 1
            continue
        data = AttrDict(
            name=row[0].value,
            hostname=row[1].value,
            port=row[2].value,
            username=row[3].value,
            desc=row[5].value
        )
        target = (data.hostname, data.port, data.username)
        if target in seen_targets or Host.objects.filter(hostname=data.hostname, port=data.port, username=data.username).exists():
            summary['skip'].append(i)
            summary['fail'] += 1
            continue
        if data.name in seen_names or Host.objects.filter(name=data.name).exists():
            summary['repeat'].append(i)
            summary['fail'] += 1
            continue
        candidates.append((data, row[4].value))
        seen_names.add(data.name)
        seen_targets.add(target)
    with access.quota_guard([request.user.pk], actor=request.user):
        access.require(request.user, 'host.host.import')
        access.require_groups(request.user, 'host', [group_id], manage=True)
        for data, password in candidates:
            host = Host.objects.create(created_by=request.user, **data)
            host.groups.add(group_id)
            host.password = password
            hosts.append(host)
        summary['success'] = len(hosts)
    token = uuid.uuid4().hex
    if hosts:
        register_resource_stream(token, request.user, [x.pk for x in hosts], 'host.host.import')
        Thread(target=batch_sync_host, args=(token, hosts), kwargs={
            'user_id': request.user.pk, 'permission': 'host.host.import'}).start()
    return json_response({'summary': summary, 'token': token, 'hosts': {x.id: {'name': x.name} for x in hosts}})


@auth('host.host.export')
def post_export(request):
    hosts = access.scope(request.user, 'host').select_related('hostextend')
    wb = Workbook()
    ws = wb.active
    is_en = request.headers.get('X-Language') == 'en'
    headers = ('主机名称', 'SSH地址', 'SSH端口', 'SSH用户', 'SSH密码', '备注信息', '实例ID', '操作系统', 'CPU核心数',
               '内存GB', '磁盘GB', '内网IP', '公网IP', '实例计费方式', '网络计费方式', '创建时间', '到期时间')
    ws.append([translate(x) for x in headers] if is_en else list(headers))
    for item in hosts:
        data = [item.name, item.hostname, item.port, item.username, '', item.desc]
        if hasattr(item, 'hostextend'):
            data.extend([
                item.hostextend.instance_id,
                item.hostextend.os_name,
                item.hostextend.cpu,
                item.hostextend.memory,
                ','.join(str(x) for x in json.loads(item.hostextend.disk)),
                ','.join(json.loads(item.hostextend.private_ip_address)),
                ','.join(json.loads(item.hostextend.public_ip_address)),
                translate_choice(item.hostextend.get_instance_charge_type_display()) if is_en
                else item.hostextend.get_instance_charge_type_display(),
                translate_choice(item.hostextend.get_internet_charge_type_display()) if is_en
                else item.hostextend.get_internet_charge_type_display(),
                item.hostextend.created_time,
                item.hostextend.expired_time
            ])
        ws.append(data)
    response = HttpResponse(content_type='application/octet-stream')
    wb.save(response)
    return response


@auth('host.host.add')
def post_parse(request):
    file = request.FILES['file']
    if file:
        data = file.read()
        return json_response(data.decode())
    else:
        return HttpResponseBadRequest()


@auth('host.host.verify')
def batch_valid(request):
    form, error = JsonParser(
        Argument('password', required=False),
        Argument('range', filter=lambda x: x in ('1', '2'), help='参数错误')
    ).parse(request.body)
    if error is None:
        if form.range == '1':  # all hosts
            hosts = access.scope(request.user, 'host', manage=True)
        else:
            hosts = access.scope(request.user, 'host', manage=True).filter(is_verified=False)
        hosts = list(hosts)
        token = uuid.uuid4().hex
        register_resource_stream(token, request.user, [x.pk for x in hosts], 'host.host.verify')
        Thread(target=batch_sync_host, args=(token, hosts, form.password), kwargs={
            'user_id': request.user.pk, 'permission': 'host.host.verify'}).start()
        return json_response({'token': token, 'hosts': {x.id: {'name': x.name} for x in hosts}})
    return json_response(error=error)


def _do_host_verify(form, allow_global=True, authorize=None):
    authorize = authorize or (lambda: None)
    authorize()
    password = form.pop('password')
    if form.pkey:
        try:
            with SSH(form.hostname, form.port, form.username, form.pkey, authorize=authorize) as ssh:
                authorize()
                ssh.ping()
            return True
        except BadAuthenticationType:
            raise Exception('该主机不支持密钥认证，请参考官方文档，错误代码：E01')
        except AuthenticationException:
            raise Exception('上传的独立密钥认证失败，请检查该密钥是否能正常连接主机（推荐使用全局密钥）')
        except socket.timeout:
            raise Exception('连接主机超时，请检查网络')

    private_key, public_key = AppSetting.get_ssh_key() if allow_global else SSH.generate_key()
    if not allow_global:
        form.pkey = private_key
    if password:
        try:
            with SSH(form.hostname, form.port, form.username, password=password, authorize=authorize) as ssh:
                authorize()
                ssh.add_public_key(public_key)
        except BadAuthenticationType:
            raise Exception('该主机不支持密码认证，请参考官方文档，错误代码：E00')
        except AuthenticationException:
            raise Exception('密码连接认证失败，请检查密码是否正确')
        except socket.timeout:
            raise Exception('连接主机超时，请检查网络')

    try:
        authorize()
        with SSH(form.hostname, form.port, form.username, private_key, authorize=authorize) as ssh:
            authorize()
            ssh.ping()
    except BadAuthenticationType:
        raise Exception('该主机不支持密钥认证，请参考官方文档，错误代码：E01')
    except AuthenticationException:
        if password:
            raise Exception('密钥认证失败，请参考官方文档，错误代码：E02')
        return False
    except socket.timeout:
        raise Exception('连接主机超时，请检查网络')
    return True
