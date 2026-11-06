# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.conf import settings
from django_redis import get_redis_connection
from apps.apis.utils import api_key_required, api_response, parse_json_body
from apps.exec.models import ExecTemplate
from apps.host.models import Host
from apps.account import access
from libs.validators import is_env_name
import json
import uuid

RESULT_TTL = 3600


def _result_key(token):
    return f'{settings.EXEC_RESULT_KEY}:{token}'


def authorize(template, ids):
    current = ExecTemplate.objects.get(pk=template.pk)
    access.require(current.created_by_id, 'exec.task.do')
    access.require_resources(current.created_by_id, 'host', json.loads(current.host_ids), manage=True)
    access.require_resources(current.created_by_id, 'host', list(ids), manage=True)


@api_key_required
def trigger(request, template_id):
    if request.method != 'POST':
        return api_response(error='Method not allowed', status=405)
    body = parse_json_body(request)
    if body is None:
        return api_response(error='Invalid json body', status=400)
    template = ExecTemplate.objects.filter(pk=template_id).first()
    if not template:
        return api_response(error='Template not found', status=404)

    # 开放服务没有登录用户，只能以模板预设的主机为边界：host_ids 只能缩小范围，不能扩大
    allowed_ids = set(json.loads(template.host_ids))
    if not allowed_ids:
        return api_response(error='The template has no default hosts', status=400)
    host_ids = body.get('host_ids') or list(allowed_ids)
    if not isinstance(host_ids, list) or not all(isinstance(x, int) for x in host_ids):
        return api_response(error='host_ids must be a list of host id', status=400)
    if not set(host_ids).issubset(allowed_ids):
        return api_response(error=f'Host not in template: {sorted(set(host_ids) - allowed_ids)}', status=403)
    try:
        authorize(template, allowed_ids)
    except (ValueError, PermissionError):
        return api_response(error='Template owner is inactive or no longer authorized for all targets', status=403)
    hosts = list(Host.objects.filter(id__in=host_ids))
    missing = set(host_ids) - {x.id for x in hosts}
    if missing:
        return api_response(error=f'Host not found: {sorted(missing)}', status=400)

    params = body.get('params') or {}
    if not isinstance(params, dict):
        return api_response(error='params must be an object', status=400)
    if not all(map(is_env_name, params)):
        return api_response(error='Invalid param name', status=400)
    for item in json.loads(template.parameters):
        if item['variable'] not in params and item.get('default') not in (None, ''):
            params[item['variable']] = item['default']
        if item.get('required') and params.get(item['variable']) in (None, ''):
            return api_response(error=f'Missing required param: {item["variable"]}', status=400)

    token, rds = uuid.uuid4().hex, get_redis_connection()
    meta = {'template_id': template.id, 'hosts': {x.id: f'{x.name}({x.hostname}:{x.port})' for x in hosts}}
    rds.hset(_result_key(token), 'meta', json.dumps(meta))
    rds.expire(_result_key(token), RESULT_TTL)
    for host in hosts:
        try:
            authorize(template, allowed_ids)
        except (ValueError, PermissionError):
            return api_response(error='Authorization changed; further dispatch stopped', status=403)
        data = dict(
            key=host.id,
            name=host.name,
            token=token,
            interpreter=template.interpreter,
            hostname=host.hostname,
            port=host.port,
            username=host.username,
            command=template.body,
            pkey=host.private_key,
            params=params,
            record=True,
            template_id=template.pk,
        )
        rds.rpush(settings.EXEC_WORKER_KEY, json.dumps(data))
    return api_response({'token': token}, status=202)


@api_key_required
def result(request, token):
    rds = get_redis_connection()
    raw = rds.hgetall(_result_key(token))
    if not raw:
        return api_response(error='Token not found or expired', status=404)
    raw = {k.decode(): v.decode() for k, v in raw.items()}
    meta = json.loads(raw['meta'])
    template = ExecTemplate.objects.filter(pk=meta.get('template_id')).first()
    try:
        if template is None:
            raise ValueError('Template unavailable')
        authorize(template, [int(pk) for pk in meta['hosts']])
    except (ValueError, PermissionError):
        return api_response(error='Template owner is inactive or no longer authorized for all targets', status=403)
    hosts, state = [], 'success'
    for host_id, title in meta['hosts'].items():
        code = raw.get(f'status:{host_id}')
        output = rds.get(f'{_result_key(token)}:{host_id}')
        if code is None:
            status = state = 'running'
        elif code == '0':
            status = 'success'
        else:
            status = 'failed'
            if state != 'running':
                state = 'failed'
        hosts.append(dict(
            id=int(host_id),
            title=title,
            status=status,
            exit_code=None if code is None else int(code),
            output=output.decode(errors='replace') if output else ''
        ))
    try:
        authorize(template, [int(pk) for pk in meta['hosts']])
    except (ValueError, PermissionError):
        return api_response(error='Authorization changed; result access denied', status=403)
    return api_response({'status': state, 'hosts': hosts})
