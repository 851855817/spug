"""Resource scope supplements, rather than replaces, app/environment access."""
from functools import wraps
import json
import hashlib
import re
from django.core.exceptions import ObjectDoesNotExist
from django.views import View
from apps.account import access
from libs import json_response


def authorize(user, record, manage=False, code=None, owned=False):
    current = access.require(user, code) if code else access.current_user(user)
    deploy = getattr(record, 'deploy', record)
    if not current.is_supper:
        perms = current.deploy_perms
        if deploy.app_id not in perms['apps'] or deploy.env_id not in perms['envs']:
            access.reject(current, '应用或环境无权访问')
        if owned and record.created_by_id != current.pk:
            access.reject(current, '记录不存在或无权访问')
    if manage or not current.is_supper:
        ids = access.identifiers(json.loads(deploy.host_ids))
        if hasattr(record, 'host_ids'):
            ids.update(access.identifiers(json.loads(record.host_ids)))
        access.require_resources(current, 'host', list(ids), manage=manage)
    if manage and not current.is_supper and hasattr(deploy, 'extend_obj'):
        extend = deploy.extend_obj
        if deploy.extend == '1':
            local_commands = bool(extend.hook_pre_server or extend.hook_post_server)
        else:
            actions = json.loads(extend.host_actions)
            local_commands = bool(json.loads(extend.server_actions)) or any(
                item.get('type') == 'transfer' and item.get('src_mode') != '1' for item in actions)
        if local_commands:
            access.reject(current, '发布包含平台本机脚本或本机文件来源，需要平台管理员执行')
    return current


def application(user, app_id, manage=False):
    from apps.app.models import App, Deploy
    current = access.current_user(user)
    if not current.is_supper and app_id not in current.deploy_perms['apps']:
        access.reject(current, '应用不存在或无权访问')
    if not App.objects.filter(pk=app_id).exists():
        access.reject(current, '应用不存在或无权访问')
    for deploy in Deploy.objects.filter(app_id=app_id):
        authorize(current, deploy, manage=manage)
    return current


def _signature(record):
    deploy = getattr(record, 'deploy', record)
    values = {field.name: getattr(deploy, field.name) for field in deploy._meta.fields
              if field.name not in ('created_by', 'created_at') and not field.is_relation}
    values.update(app_id=deploy.app_id, env_id=deploy.env_id,
                  extend=deploy.extend_obj.to_dict(),
                  record={name: getattr(record, name, None) for name in
                          ('host_ids', 'extra', 'version', 'spug_version', 'created_by_id', 'do_by_id')})
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def require_upload(user, deploy_id, extra, legacy_admin=False):
    from .models import DeployUpload
    current = access.current_user(user)
    if (not isinstance(extra, dict) or not isinstance(extra.get('path'), str) or
            not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', extra['path']) or
            not isinstance(extra.get('name'), str) or not extra['name'] or
            extra['name'] in ('.', '..') or '/' in extra['name'] or '\\' in extra['name']):
        raise ValueError('上传文件信息无效，请重新上传')
    upload = DeployUpload.objects.filter(deploy_id=deploy_id, path=extra['path'], name=extra['name']).first()
    if upload is None and legacy_admin and current.is_supper:
        return
    if upload is None or (not current.is_supper and upload.owner_id != current.pk):
        access.reject(current, '上传文件不存在或不属于当前账号，请重新上传')
    return upload


def issue_config_token(rds, token, record, actor):
    user = access.current_user(actor)
    rds.setex(token, 3600, json.dumps({'record_type': type(record).__name__, 'record_id': record.pk,
        'actor_id': user.pk, 'signature': _signature(record)}))


def config_token_scope(meta):
    from .models import DeployRequest
    from apps.repository.models import Repository
    models = {'DeployRequest': DeployRequest, 'Repository': Repository}
    if not isinstance(meta, dict) or meta.get('record_type') not in models:
        raise ValueError('无归属的旧令牌已失效')
    record = models[meta['record_type']].objects.get(pk=meta['record_id'])
    actor = getattr(record, 'do_by_id', None) or record.created_by_id
    if actor != meta['actor_id'] or _signature(record) != meta['signature']:
        raise ValueError('令牌对应配置或账号已改变')
    execution_guard(record)()
    return record.deploy.app, record.deploy.env_id


def execution_guard(record, targets=None, parent=None):
    """Resolve the saved actor and snapshot again before every external action."""
    model, pk, signature = type(record), record.pk, _signature(record)

    def check():
        parent_actor = parent() if parent is not None else None
        current_record = model.objects.filter(pk=pk).first() if pk else record
        if current_record is None or _signature(current_record) != signature:
            access.reject(None, '发布配置或执行身份已变化，请重新创建任务')
        actor_id = parent_actor or getattr(current_record, 'do_by_id', None) or current_record.created_by_id
        if model.__name__ == 'DeployRequest' or parent is not None:
            code = 'deploy.request.do'
        else:
            user, codes = access.permissions(actor_id)
            if not user.is_supper and not codes.intersection({'deploy.repository.add', 'deploy.repository.build'}):
                access.reject(user, '没有构建权限')
            code = None
        user = authorize(actor_id, current_record, manage=True, code=code, owned=True)
        version = getattr(current_record, 'spug_version', None)
        if version is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', version):
            access.reject(user, '构建文件路径无效')
        if current_record.deploy.extend == '2' and current_record.deploy.extend_obj.require_upload:
            require_upload(user, current_record.deploy_id, json.loads(current_record.extra), legacy_admin=True)
        if targets is not None:
            selected = access.identifiers(targets)
            saved = access.identifiers(json.loads(current_record.host_ids))
            if not selected.issubset(saved):
                access.reject(user, '执行目标不属于保存的发布申请')
            access.require_resources(user, 'host', list(selected), manage=True)
        return user

    check()
    return check


def visible(user, queryset, owned=False):
    current = access.current_user(user)
    if current.is_supper:
        return queryset
    perms, allowed, ids = current.deploy_perms, access.allowed_ids(current, 'host'), []
    for row in queryset:
        deploy = getattr(row, 'deploy', row)
        if (deploy.app_id not in perms['apps'] or deploy.env_id not in perms['envs'] or
                (owned and row.created_by_id != current.pk)):
            continue
        try:
            targets = access.identifiers(json.loads(deploy.host_ids))
            if hasattr(row, 'host_ids'):
                targets.update(access.identifiers(json.loads(row.host_ids)))
            if targets.issubset(allowed):
                ids.append(row.pk)
        except (TypeError, ValueError):
            continue
    return queryset.filter(pk__in=ids)


def validate_targets(user, deploy, targets):
    authorize(user, deploy, manage=True)
    selected = access.identifiers(targets)
    if not selected or not selected.issubset(access.identifiers(json.loads(deploy.host_ids))):
        raise ValueError('目标必须属于该发布配置')
    access.require_resources(user, 'host', list(selected), manage=True)


def errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, PermissionError) as error:
            return json_response(error=str(error))
        except ObjectDoesNotExist:
            return json_response(error='记录不存在或无权访问')
    return call


class ResourceView(View):
    @errors
    def dispatch(self, request, *args, **kwargs):
        access.current_user(request.user)
        return super().dispatch(request, *args, **kwargs)
