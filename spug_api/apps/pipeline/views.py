# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django.conf import settings
from django.http.response import HttpResponseBadRequest
from django_redis import get_redis_connection
from redis.exceptions import LockError
from libs import JsonParser, Argument, json_response, auth
from libs.utils import AttrDict
from libs.locale import get_request_language
from libs.gitlib import RemoteGit
from apps.pipeline.helper import Helper
from apps.pipeline.models import Pipeline, PipeHistory
from apps.pipeline.utils import NodeExecutor, fill_node_targets
from apps.credential.models import Credential
from threading import Thread
from pathlib import Path
from uuid import uuid4
import copy
import json
import re
from types import SimpleNamespace
from apps.account import access
from . import resources


class ResourceView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            access.current_user(request.user)
            return super().dispatch(request, *args, **kwargs)
        except (ValueError, PermissionError) as error:
            return json_response(error=str(error))
        except (Pipeline.DoesNotExist, PipeHistory.DoesNotExist):
            return json_response(error='流程或记录不存在')
        except LockError:
            return json_response(error='上传或执行确认正在处理，请稍后重试')


class PipeView(ResourceView):
    @auth('pipeline.pipeline.view|pipeline.pipeline.edit|pipeline.pipeline.do')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False)
        ).parse(request.GET)
        if error is None:
            if form.id:
                pipe = Pipeline.objects.filter(pk=form.id).first()
                if not pipe:
                    return json_response(error='未找到指定流程')
                resources.authorize(request.user, pipe)
                response = pipe.to_view()
            else:
                pipes = list(resources.visible(request.user, Pipeline.objects.all()))
                latest_map = {}
                for record in resources.visible(request.user, PipeHistory.objects.filter(pipeline_id__in=[x.id for x in pipes])):
                    latest_map.setdefault(record.pipeline_id, record)
                response = [x.to_list(latest_map.get(x.id)) for x in pipes]
            return json_response(response)
        return json_response(error=error)

    @auth('pipeline.pipeline.add|pipeline.pipeline.edit')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('name', help='请输入流程名称'),
            Argument('nodes', type=list, handler=json.dumps, default='[]')
        ).parse(request.body)
        if error is None:
            access.require(request.user, 'pipeline.pipeline.edit' if form.id else 'pipeline.pipeline.add')
            resources.authorize(request.user, SimpleNamespace(created_by_id=request.user.pk, nodes=form.nodes), manage=True)
            if form.id:
                resources.authorize(request.user, Pipeline.objects.get(pk=form.id), manage=True)
                Pipeline.objects.filter(pk=form.id).update(**form)
                pipe = Pipeline.objects.get(pk=form.id)
            else:
                pipe = Pipeline.objects.create(created_by=request.user, **form)
            return json_response(pipe.to_view())
        return json_response(error=error)

    @auth('pipeline.pipeline.edit')
    def patch(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定操作对象'),
            Argument('name', required=False),
            Argument('nodes', type=list, handler=json.dumps, required=False),
        ).parse(request.body, True)
        if error is None:
            pipe = Pipeline.objects.get(pk=form.id)
            resources.authorize(request.user, pipe, manage=True)
            if 'nodes' in form:
                resources.authorize(request.user, SimpleNamespace(created_by_id=request.user.pk, nodes=form.nodes), manage=True)
            Pipeline.objects.filter(pk=form.id).update(**form)
        return json_response(error=error)

    @auth('pipeline.pipeline.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定操作对象')
        ).parse(request.GET)
        if error is None:
            # 逐条删除而不是 queryset.delete()，后者不会触发执行记录的输出文件清理
            pipe = Pipeline.objects.filter(pk=form.id).first()
            if pipe:
                resources.authorize(request.user, pipe, manage=True)
                pipe.delete()
        return json_response(error=error)


class DoView(ResourceView):
    @auth('pipeline.pipeline.do')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
        ).parse(request.body)
        if error is None:
            pipe = Pipeline.objects.get(pk=form.id)
            resources.authorize(request.user, pipe, manage=True, code='pipeline.pipeline.do')
            nodes = fill_node_targets(json.loads(pipe.nodes))

            dynamic_params = []
            for item in nodes:
                if item.get('module') == 'parameter':
                    if item.get('dynamic_params'):
                        dynamic_params.extend(item['dynamic_params'])
                elif item.get('module') == 'build':
                    if item.get('git_mode') == 'tag':
                        if item.get('git_tag') == 'selective':
                            credential = None
                            if item.get('credential_id'):
                                credential = resources.credential(request.user, item['credential_id'])
                            def authorize():
                                current = Pipeline.objects.get(pk=pipe.pk)
                                if resources.definition_digest(current) != resources.definition_digest(pipe):
                                    access.reject(request.user, '流水线配置已改变，请重试')
                                return resources.authorize(request.user, current, manage=True, code='pipeline.pipeline.do')
                            is_pass, tags = RemoteGit.fetch_remote_tags(item.get('git_url'), credential, authorize=authorize)
                            if is_pass and tags:
                                options = [{'value': x, 'label': x} for x in tags]
                                dynamic_params.append(
                                    {'variable': '_spug_git_tag', 'name': 'Git标签', 'type': 'select',
                                     'options': options, 'required': True})
                            else:
                                dynamic_params.append(
                                    {'variable': '_spug_git_tag', 'name': 'Git标签', 'type': 'text',
                                     'required': True, 'help': '请输入要构建的Git标签名称'})
                    elif item.get('git_commit') == 'selective':
                        dynamic_params.append(
                            {'variable': '_spug_git_commit', 'name': 'Git Commit', 'type': 'text', 'required': False,
                             'help': '要构建的Commit ID，留空则使用所选分支的最新提交'})
                elif item.get('module') == 'data_upload':
                    tmp = {'variable': item['id'], 'name': item['name'], 'type': 'upload', 'required': True}
                    if item.get('accept'):
                        tmp['accept'] = item['accept']
                    if item.get('size'):
                        tmp['size'] = item['size']
                    dynamic_params.append(tmp)

            token = uuid4().hex
            if dynamic_params:
                get_redis_connection().set('pipeline-ticket:' + token, json.dumps({
                    'owner_id': request.user.pk, 'pipeline_id': pipe.pk, 'digest': resources.definition_digest(pipe)}), 3600)
                response = AttrDict(token=token, nodes=nodes, dynamic_params=dynamic_params)
            else:
                record = PipeHistory.make(pipe, nodes, request.user, token)
                rds = get_redis_connection()
                executor = NodeExecutor(rds, token, json.loads(pipe.nodes), pipe_name=pipe.name,
                                        language=get_request_language(request), history_id=record.id)
                Thread(target=executor.run).start()
                response = AttrDict(token=token, nodes=nodes)
            return json_response(response)
        return json_response(error=error)

    @auth('pipeline.pipeline.do')
    def patch(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('token', help='参数错误'),
            Argument('params', type=dict, help='参数错误'),
        ).parse(request.body)
        if error is None:
            for k, v in form.params.items():
                if isinstance(v, list):
                    form.params[k] = ','.join(str(x) for x in v)
            pipe = Pipeline.objects.get(pk=form.id)
            resources.authorize(request.user, pipe, manage=True, code='pipeline.pipeline.do')
            nodes = json.loads(pipe.nodes)
            for item in nodes:
                if item.get('module') == 'parameter':
                    item['dynamic_params'] = form.params
                    break

            # 快照要带上 _targets 和本次代入的参数，否则历史控制台还原不出主机分页
            rds = get_redis_connection()
            with ticket_lock(rds, form.token):
                require_ticket(request.user, form.token, pipe)
                rds.delete('pipeline-ticket:' + form.token)
                record = PipeHistory.make(pipe, fill_node_targets(copy.deepcopy(nodes)), request.user, form.token)

            executor = NodeExecutor(rds, form.token, nodes, form.params, pipe_name=pipe.name,
                                    language=get_request_language(request), history_id=record.id)
            Thread(target=executor.run).start()
            return json_response()
        return json_response(error=error)


class HistoryView(ResourceView):
    @auth('pipeline.pipeline.view|pipeline.pipeline.edit|pipeline.pipeline.do')
    def get(self, request, h_id=None):
        if h_id:
            record = PipeHistory.objects.filter(pk=h_id).first()
            if not record:
                return json_response(error='未找到指定执行记录')
            resources.authorize(request.user, record)
            self._sync_interrupted([record])
            nodes = json.loads(record.nodes)
            outputs = {}
            for item in nodes:
                # 键的构造必须和执行器一致：节点本身用 id，多主机节点再按 id.主机id 分流
                outputs[item['id']] = {'data': '', 'status': ''}
                for host in item.get('_targets') or []:
                    outputs[f'{item["id"]}.{host["id"]}'] = {'data': '', 'status': ''}

            response = AttrDict(record.to_list())
            response.token = record.token
            response.nodes = nodes
            response.outputs = outputs
            # index 是已消费的消息数，执行中的记录据此接着 websocket 往下收
            response.index = Helper.fill_outputs(outputs, record.token) if record.token else 0
            resources.authorize(request.user, record)
            return json_response(response)

        form, error = JsonParser(
            Argument('pipeline_id', type=int, help='请指定流水线')
        ).parse(request.GET)
        if error is None:
            records = list(resources.visible(request.user, PipeHistory.objects.filter(pipeline_id=form.pipeline_id)))
            self._sync_interrupted(records)
            return json_response([x.to_list() for x in records])
        return json_response(error=error)

    @auth('pipeline.pipeline.del')
    def delete(self, request, h_id=None):
        record = PipeHistory.objects.filter(pk=h_id).first()
        if record:
            resources.authorize(request.user, record, manage=True)
            record.delete()
        return json_response()

    @staticmethod
    def _sync_interrupted(records):
        """把不可能再有结果的执行中记录收敛成已中断。

        两种情况：输出键已消失（进程重启等原因让执行线程中途没了）；以及升级前遗留的
        记录——它们没有 token，本就没有输出可回放。迁移文件不随仓库发布（migrations/ 被
        忽略，安装时才 makemigrations），所以只能在这里兜底而不是写数据迁移。
        """
        running = [x for x in records if x.status == 0]
        if not running:
            return
        rds = get_redis_connection()
        for item in running:
            if item.token and rds.exists(item.token):
                continue
            if PipeHistory.objects.filter(pk=item.id, status=0).update(status=3):
                item.status = 3


@auth('pipeline.pipeline.do')
def handle_data_upload(request):
    token = request.POST.get('token')
    node_id = request.POST.get('id')
    file = request.FILES.get('file')
    if not all([token, node_id, file]):
        return HttpResponseBadRequest('参数错误')

    try:
        with ticket_lock(get_redis_connection(), token) as lease:
            pipe = require_ticket(request.user, token)
            nodes = json.loads(pipe.nodes)
            resources.validate_nodes(nodes)
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', node_id) or
                    not any(str(node.get('id')) == node_id and node.get('module') == 'data_upload' for node in nodes) or
                    file.name in ('.', '..') or '/' in file.name or '\\' in file.name):
                raise ValueError('上传目标无效')
            file_path = Path(settings.TRANSFER_DIR) / token / node_id / file.name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'wb') as f:
                for chunk in file.chunks():
                    lease.extend(30, replace_ttl=True)
                    require_ticket(request.user, token, pipe)
                    f.write(chunk)
    except (ValueError, TypeError, LockError):
        return HttpResponseBadRequest('上传票据无效或无权访问')
    return json_response('ok')


def ticket_lock(rds, token):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('上传票据无效')
    return rds.lock('pipeline-ticket-lock:' + token, timeout=30, blocking_timeout=5)


def require_ticket(user, token, pipe=None):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('上传票据无效')
    raw = get_redis_connection().get('pipeline-ticket:' + token)
    try:
        meta = json.loads(raw)
        expected = pipe.pk if pipe is not None else meta['pipeline_id']
        pipe = Pipeline.objects.get(pk=meta['pipeline_id'])
        if (meta['owner_id'] != user.pk or meta['pipeline_id'] != pipe.pk or
                pipe.pk != expected or
                meta['digest'] != resources.definition_digest(pipe)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, Pipeline.DoesNotExist):
        raise ValueError('上传票据已过期或流程已变化') from None
    resources.authorize(user, pipe, manage=True, code='pipeline.pipeline.do')
    return pipe
