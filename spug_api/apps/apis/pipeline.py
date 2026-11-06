# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.conf import settings
from django_redis import get_redis_connection
from apps.apis.utils import api_key_required, api_response, parse_json_body
from apps.pipeline.models import Pipeline, PipeHistory
from apps.pipeline.utils import NodeExecutor, fill_node_targets
from apps.pipeline import resources
from libs.validators import is_env_name
from threading import Thread
from uuid import uuid4
import copy
import json

RESULT_TTL = 3600


def _marker_key(token):
    return f'{settings.PIPELINE_KEY}:api:{token}'


@api_key_required
def trigger(request, pipeline_id):
    if request.method != 'POST':
        return api_response(error='Method not allowed', status=405)
    body = parse_json_body(request)
    if body is None:
        return api_response(error='Invalid json body', status=400)
    pipe = Pipeline.objects.filter(pk=pipeline_id).first()
    if not pipe:
        return api_response(error='Pipeline not found', status=404)
    try:
        resources.authorize(pipe.created_by_id, pipe, manage=True, code='pipeline.pipeline.do')
    except (ValueError, PermissionError):
        return api_response(error='Pipeline owner is inactive or no longer authorized', status=403)
    nodes = json.loads(pipe.nodes)
    if not nodes:
        return api_response(error='Pipeline has no nodes', status=400)

    params = body.get('params') or {}
    if not isinstance(params, dict):
        return api_response(error='params must be an object', status=400)
    if not all(map(is_env_name, params)):
        return api_response(error='Invalid param name', status=400)
    for k, v in params.items():
        if isinstance(v, list):
            params[k] = ','.join(str(x) for x in v)

    required = []
    for item in nodes:
        module = item.get('module')
        if module == 'data_upload':
            return api_response(error='Pipeline with data upload node is not supported', status=400)
        elif module == 'parameter':
            for x in item.get('dynamic_params') or []:
                # 与页面的参数弹窗一致：未传的参数套用参数节点里配置的默认值
                if x['variable'] not in params and x.get('default') not in (None, ''):
                    params[x['variable']] = x['default']
                if x.get('required'):
                    required.append(x['variable'])
        elif module == 'build' and item.get('git_mode') == 'tag' and item.get('git_tag') == 'selective':
            required.append('_spug_git_tag')
    for key in required:
        if params.get(key) in (None, ''):
            return api_response(error=f'Missing required param: {key}', status=400)

    # 与页面执行保持一致：把参数节点的动态参数定义替换为本次传入的值
    for item in nodes:
        if item.get('module') == 'parameter':
            item['dynamic_params'] = params
            break

    token, rds = uuid4().hex, get_redis_connection()
    # API 触发没有登录用户，归到流水线创建人名下，靠 trigger 区分来源
    try:
        resources.authorize(pipe.created_by_id, pipe, manage=True, code='pipeline.pipeline.do')
    except (ValueError, PermissionError):
        return api_response(error='Authorization changed; dispatch stopped', status=403)
    record = PipeHistory.make(pipe, fill_node_targets(copy.deepcopy(nodes)), pipe.created_by, token, trigger='api')

    rds.set(_marker_key(token), json.dumps({'pipeline_id': pipe.pk, 'history_id': record.pk}), RESULT_TTL)
    executor = NodeExecutor(rds, token, nodes, params, pipe_name=pipe.name, history_id=record.id)
    executor.helper.ttl = RESULT_TTL
    Thread(target=executor.run).start()
    return api_response({'token': token}, status=202)


@api_key_required
def result(request, token):
    rds = get_redis_connection()
    raw = rds.get(_marker_key(token))
    if not raw:
        return api_response(error='Token not found or expired', status=404)
    try:
        meta = json.loads(raw)
        record = PipeHistory.objects.get(pk=meta['history_id'], token=token, pipeline_id=meta['pipeline_id'])
        resources.authorize(record.created_by_id, record, manage=True, code='pipeline.pipeline.do')
    except (ValueError, TypeError, KeyError, PipeHistory.DoesNotExist):
        return api_response(error='Result no longer authorized', status=403)

    outputs, is_running = {}, rds.ttl(token) < 0
    for item in rds.lrange(token, 0, -1):
        item = json.loads(item.decode())
        node = outputs.setdefault(str(item['key']), {'status': '', 'output': ''})
        if item.get('data'):
            node['output'] += item['data']
        if item.get('status'):
            node['status'] = item['status']

    # 键不含 '.' 的是节点本身，'节点.主机' 形式的是节点下各主机的输出
    statuses = [v['status'] for k, v in outputs.items() if '.' not in k]
    if is_running or 'processing' in statuses:
        state = 'running'
    elif 'error' in statuses:
        state = 'failed'
    else:
        state = 'success'
    try:
        record.refresh_from_db()
        resources.authorize(record.created_by_id, record, manage=True, code='pipeline.pipeline.do')
    except (ValueError, PipeHistory.DoesNotExist):
        return api_response(error='Result no longer authorized', status=403)
    return api_response({'pipeline_id': record.pipeline_id, 'status': state, 'nodes': outputs})
