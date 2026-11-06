# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django_redis import get_redis_connection
from django.conf import settings
from libs import json_response, JsonParser, Argument, human_datetime, auth
from libs.locale import get_request_language
from apps.exec.models import ExecTemplate, ExecHistory
from apps.host.models import Host
from apps.account.utils import has_host_perm
from apps.account.utils import require_owned_task, require_stream
from apps.account import access
from django.db.models import Q
import uuid
import json
import os


class TemplateView(View):
    @auth('exec.template.view|exec.task.do|schedule.schedule.add|schedule.schedule.edit|\
    monitor.monitor.add|monitor.monitor.edit')
    def get(self, request):
        templates = ExecTemplate.objects.all()
        if not request.user.is_supper:
            templates = templates.filter(Q(created_by=request.user) | Q(created_by__is_supper=True))
        # Public templates never disclose inaccessible default targets.
        visible = access.allowed_ids(request.user, 'host')
        values = []
        for template in templates:
            item = template.to_view()
            item['host_ids'] = [x for x in item['host_ids'] if x in visible]
            values.append(item)
        types = [x['type'] for x in templates.order_by('type').values('type').distinct()]
        return json_response({'types': types, 'templates': values})

    @auth('exec.template.add|exec.template.edit')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('name', help='请输入模版名称'),
            Argument('type', help='请选择模版类型'),
            Argument('body', help='请输入模版内容'),
            Argument('interpreter', default='sh'),
            Argument('host_ids', type=list, handler=json.dumps, default=[]),
            Argument('parameters', type=list, handler=json.dumps, default=[]),
            Argument('desc', required=False)
        ).parse(request.body)
        if error is None:
            access.require(request.user, 'exec.template.edit' if form.id else 'exec.template.add')
            access.require_resources(request.user, 'host', json.loads(form.host_ids), manage=True)
            if form.id:
                template = ExecTemplate.objects.filter(pk=form.id).first()
                if not template or (not request.user.is_supper and template.created_by_id != request.user.pk):
                    access.reject(request.user, '模板不存在或无权修改')
                form.updated_at = human_datetime()
                form.updated_by = request.user
                ExecTemplate.objects.filter(pk=form.pop('id')).update(**form)
            else:
                form.created_by = request.user
                ExecTemplate.objects.create(**form)
        return json_response(error=error)

    @auth('exec.template.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定操作对象')
        ).parse(request.GET)
        if error is None:
            templates = ExecTemplate.objects.filter(pk=form.id)
            if not request.user.is_supper:
                templates = templates.filter(created_by=request.user)
            if not templates.exists():
                access.reject(request.user, '模板不存在或无权修改')
            templates.delete()
        return json_response(error=error)


class TaskView(View):
    @auth('exec.task.do')
    def get(self, request):
        records = ExecHistory.objects.select_related('template')
        if not request.user.is_supper:
            records = records.filter(user=request.user)
        allowed = access.allowed_ids(request.user, 'host', manage=True)
        data = []
        for record in records:
            if not set(json.loads(record.host_ids)).issubset(allowed):
                continue
            if (record.template and not request.user.is_supper and
                    record.template.created_by_id != request.user.pk and not record.template.created_by.is_supper):
                # Historical cross-owner references may not expose a live private template.
                record.template = None
            data.append(record.to_view())
        return json_response(data)

    @auth('exec.task.do')
    def post(self, request):
        form, error = JsonParser(
            Argument('host_ids', type=list, filter=lambda x: len(x), help='请选择执行主机'),
            Argument('command', help='请输入执行命令内容'),
            Argument('interpreter', default='sh'),
            Argument('template_id', type=int, required=False),
            Argument('params', type=dict, handler=json.dumps, default={})
        ).parse(request.body)
        if error is None:
            if not has_host_perm(request.user, form.host_ids, manage=True):
                return json_response(error='无权访问主机，请联系管理员')
            token, rds = uuid.uuid4().hex, get_redis_connection()
            form.host_ids.sort()
            if form.template_id:
                template = ExecTemplate.objects.filter(pk=form.template_id).first()
                if template and not request.user.is_supper and template.created_by_id != request.user.pk and not template.created_by.is_supper:
                    access.reject(request.user, '模板不存在或无权使用')
                if not template or template.body != form.command:
                    form.template_id = None

            ExecHistory.objects.create(
                user=request.user,
                digest=token,
                interpreter=form.interpreter,
                template_id=form.template_id,
                command=form.command,
                host_ids=json.dumps(form.host_ids),
                params=form.params
            )
            return json_response(token)
        return json_response(error=error)

    @auth('exec.task.do')
    def patch(self, request):
        form, error = JsonParser(
            Argument('token', help='参数错误'),
            Argument('host_ids', type=list, required=False),
            Argument('cols', type=int, required=False),
            Argument('rows', type=int, required=False)
        ).parse(request.body)
        if error is None:
            term = None
            if form.cols and form.rows:
                term = {'width': form.cols, 'height': form.rows}
            task = ExecHistory.objects.filter(digest=form.token).first()
            if task is None:
                access.reject(request.user, '记录不存在或无权访问')
            require_owned_task(request.user, task, 'exec.task.do')
            rds = get_redis_connection()
            host_ids = json.loads(task.host_ids)
            if form.host_ids:
                access.identifiers(form.host_ids)
                if not set(form.host_ids).issubset(host_ids):
                    access.reject(request.user, '重试目标必须属于原任务')
                # 重试：只重新下发指定主机，且限定在该任务的主机范围内
                host_ids = [x for x in host_ids if x in form.host_ids]
                if not host_ids:
                    return json_response(error='未找到可重试的主机')
            for host in Host.objects.filter(id__in=host_ids):
                require_owned_task(request.user, task, 'exec.task.do')
                data = dict(
                    actor_id=request.user.pk,
                    key=host.id,
                    name=host.name,
                    token=task.digest,
                    interpreter=task.interpreter,
                    hostname=host.hostname,
                    port=host.port,
                    username=host.username,
                    command=task.command,
                    pkey=host.private_key,
                    params=json.loads(task.params),
                    term=term,
                    language=get_request_language(request),
                )
                rds.rpush(settings.EXEC_WORKER_KEY, json.dumps(data))
        return json_response(error=error)


@auth('exec.task.do|deploy.request.do|deploy.repository.add|deploy.repository.build|pipeline.pipeline.do')
def handle_terminate(request):
    form, error = JsonParser(
        Argument('token', help='参数错误'),
        Argument('target', required=False)
    ).parse(request.body)
    if error is None:
        rds = get_redis_connection()
        module = None
        stream_token = form.token
        if form.token.startswith(settings.REQUEST_KEY + ':'):
            module = 'request'
        elif form.token.startswith(settings.BUILD_KEY + ':'):
            module = 'build'
        elif '.' in form.token:
            module, stream_token = 'pipeline', form.token.split('.', 1)[0]
        def authorize_termination():
            require_stream(request.user, stream_token, rds, module=module)
            action = {'request': 'deploy.request.do', 'build': 'deploy.repository.build',
                      'pipeline': 'pipeline.pipeline.do'}.get(module)
            if action:
                access.require(request.user, action)
        authorize_termination()
        # two addressing styles are used across the app:
        # - pipeline stores '{host_id}.{pid}' at key '{token}.{node_id}.{host_id}' (no target)
        # - exec/deploy/build store the raw pid at key 'PID:{token}:{target}'
        rds_key = f'PID:{form.token}:{form.target}' if form.target is not None else form.token
        pid_str = rds.get(rds_key)
        if not pid_str:
            return json_response(error='未找到关联进程')
        pid_str = pid_str.decode()
        if '.' in pid_str:
            target, pid = pid_str.split('.', 1)
        else:
            target, pid = str(form.target), pid_str
        if target.isdigit():
            authorize_termination()
            host = access.require_resources(request.user, 'host', [int(target)], manage=True).get()
            with host.get_ssh(authorize=authorize_termination) as ssh:
                authorize_termination()
                access.require_resources(request.user, 'host', [int(target)], manage=True)
                ssh.terminate(pid)
        elif target == 'local':
            if module not in ('build', 'request', 'pipeline') and not request.user.is_supper:
                access.reject(request.user, '无权终止本地进程')
            authorize_termination()
            try:
                os.killpg(os.getpgid(int(pid)), 9)
            except ProcessLookupError:
                pass  # 进程已自行退出，视为终止成功，继续清理 redis 键
        else:
            return json_response(error='未找到关联进程')
        rds.delete(rds_key)
    return json_response(error=error)
