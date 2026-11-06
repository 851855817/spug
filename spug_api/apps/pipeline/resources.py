"""Account and resource checks shared by definitions, history and workers."""
import hashlib
import json
import re

from apps.account import access
from apps.credential.models import Credential


def validate_nodes(nodes):
    nodes = json.loads(nodes) if isinstance(nodes, str) else nodes
    if not isinstance(nodes, list) or len(nodes) > 200:
        raise ValueError('流水线节点配置无效')
    seen = set()
    for node in nodes:
        if (not isinstance(node, dict) or type(node.get('id')) not in (str, int) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(node['id'])) or str(node['id']) in seen):
            raise ValueError('流水线节点编号必须唯一且仅含字母、数字、下划线或短横线')
        seen.add(str(node['id']))
    return nodes


def authorize(user, record, manage=False, code=None):
    current = access.require(user, code) if code else access.current_user(user)
    if not current.is_supper and record.created_by_id != current.pk:
        access.reject(current, '流水线不存在或无权访问')
    from .utils import resource_host_ids
    if manage or not current.is_supper:
        access.require_resources(current, 'host', list(resource_host_ids(record)), manage=manage)
    if manage:
        nodes = validate_nodes(record.nodes)
        for node in nodes:
            if node.get('credential_id'):
                credential(current, node['credential_id'])
    return current


def credential(user, pk):
    current = access.current_user(user)
    if type(pk) is not int or pk <= 0:
        raise ValueError('凭据编号无效')
    result = Credential.objects.filter(pk=pk).first()
    if result is None or not (current.is_supper or result.is_public or result.created_by_id == current.pk):
        access.reject(current, '凭据不存在或无权使用')
    return result


def visible(user, queryset):
    current = access.current_user(user)
    if current.is_supper:
        return queryset
    from .utils import resource_host_ids
    allowed, ids = access.allowed_ids(current, 'host'), []
    for record in queryset.filter(created_by_id=current.pk):
        try:
            if resource_host_ids(record).issubset(allowed):
                ids.append(record.pk)
        except (ValueError, TypeError, KeyError):
            continue
    return queryset.filter(pk__in=ids)


def definition_digest(pipe):
    return hashlib.sha256(pipe.nodes.encode()).hexdigest()


def execution_digest(nodes):
    nodes = json.loads(nodes) if isinstance(nodes, str) else nodes
    normalized = [{k: v for k, v in node.items() if k != '_targets'} for node in nodes]
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
