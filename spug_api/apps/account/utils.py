# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from apps.account import access
import json
import re


class AuthorizedSSH:
    """Revalidate before a remote method and before releasing its result."""
    def __init__(self, ssh, authorize):
        self._ssh, self._authorize = ssh, authorize
        ssh.authorize = authorize

    def __getattr__(self, name):
        target = getattr(self._ssh, name)
        if not callable(target):
            return target
        def call(*args, **kwargs):
            self._authorize()
            result = target(*args, **kwargs)
            self._authorize()
            return result
        return call


def get_host_perms(user, manage=False):
    return access.allowed_ids(user, 'host', manage=manage)


def has_host_perm(user, target, manage=False):
    try:
        targets = target if isinstance(target, (list, set, tuple)) else [int(target)]
        access.require_resources(user, 'host', list(targets), manage=manage)
        return True
    except (ValueError, TypeError):
        return False


def require_owned_task(user, task, code, manage=True):
    current = access.require(user, code)
    owner_id = getattr(task, 'user_id', getattr(task, 'created_by_id', None))
    if not current.is_supper and owner_id != current.pk:
        access.reject(current, '记录不存在或无权访问')
    ids = json.loads(task.host_ids)
    if getattr(task, 'host_id', None):
        ids = ids + [task.host_id]
    access.require_resources(current, 'host', ids, manage=manage)
    return current


def register_resource_stream(token, user, host_ids, code, manage=True, rds=None):
    from django_redis import get_redis_connection
    current = access.require(user, code)
    access.require_resources(current, 'host', host_ids, manage=manage)
    rds = rds if rds is not None else get_redis_connection()
    rds.set('resource-stream:' + token, json.dumps({
        'owner_id': current.pk, 'host_ids': host_ids, 'code': code, 'manage': manage}), ex=3600)


def require_stream(user, token, rds, module=None):
    """Bind output addresses to a persistent job or server-issued owner metadata."""
    from django.conf import settings
    from apps.exec.models import ExecHistory, Transfer
    current = access.current_user(user)
    if module in (None, 'raw'):
        if token.startswith(settings.REQUEST_KEY + ':'):
            module = 'request'
        elif token.startswith(settings.BUILD_KEY + ':'):
            module = 'build'
    if module in ('build', 'request'):
        prefix = settings.BUILD_KEY if module == 'build' else settings.REQUEST_KEY
        if not token.startswith(prefix + ':') or not token[len(prefix) + 1:].isdigit():
            access.reject(current, '输出地址无效')
        pk = int(token[len(prefix) + 1:])
        if module == 'request':
            from apps.deploy.models import DeployRequest
            task = DeployRequest.objects.filter(pk=pk).first()
            code = 'deploy.request.view'
        else:
            from apps.repository.models import Repository
            task = Repository.objects.select_related('deploy').filter(pk=pk).first()
            code = 'deploy.repository.view'
        if task is None:
            access.reject(current, '输出不存在或无权访问')
        if not current.is_supper:
            deploy = task.deploy
            deploy_perms = current.deploy_perms
            if deploy.app_id not in deploy_perms['apps'] or deploy.env_id not in deploy_perms['envs']:
                access.reject(current, '应用或环境无权访问')
        if module == 'build':
            access.require(current, code)
            if not current.is_supper and task.created_by_id != current.pk:
                access.reject(current, '输出不存在或无权访问')
            access.require_resources(current, 'host', json.loads(task.deploy.host_ids))
            return current
        return require_owned_task(current, task, code, manage=False)
    if module == 'pipeline':
        from apps.pipeline.models import PipeHistory
        from apps.pipeline.utils import resource_host_ids
        task = PipeHistory.objects.filter(token=token).first()
        current, codes = access.permissions(current)
        if not current.is_supper and not codes.intersection({'pipeline.pipeline.view', 'pipeline.pipeline.edit', 'pipeline.pipeline.do'}):
            access.reject(current, '权限拒绝')
        if not task or (not current.is_supper and task.created_by_id != current.pk):
            access.reject(current, '输出不存在或无权访问')
        ids = list(resource_host_ids(task))
        access.require_resources(current, 'host', ids)
        return current
    for model, code in ((ExecHistory, 'exec.task.do'), (Transfer, 'exec.transfer.do')):
        task = model.objects.filter(digest=token).first()
        if task:
            return require_owned_task(current, task, code)
    raw = rds.get('resource-stream:' + token)
    if raw:
        try:
            meta = json.loads(raw)
            if not current.is_supper and meta['owner_id'] != current.pk:
                access.reject(current, '输出不存在或无权访问')
            access.require(current, meta['code'])
            access.require_resources(current, 'host', meta['host_ids'], manage=meta['manage'])
            return current
        except (KeyError, TypeError, json.JSONDecodeError):
            access.reject(current, '输出地址无效')
    if not current.is_supper:
        access.reject(current, '输出不存在或无权访问')
    return current


def verify_password(password):
    if len(password) < 8:
        return False
    if not all(map(lambda x: re.findall(x, password), ['[0-9]', '[a-z]', '[A-Z]'])):
        return False
    return True
