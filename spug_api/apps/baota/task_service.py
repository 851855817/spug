"""Durable task commands. Browser input can never supply a dispatch snapshot."""
import copy
import json
import re

from django.db import transaction
from django.utils import timezone

from .models import Panel, PanelGroup, PanelTask, PanelTaskTarget, PanelLock
from .operations import get_operation
from .operations.base import strict
from .resources import SUPPORTED_VERSIONS
from .validation import positive_id


MAX_PANELS = 50
MAX_CHILDREN = 200
MAX_JSON_BYTES = 1024 * 1024
TERMINAL = frozenset(('success', 'failed', 'already', 'skipped', 'cancelled'))
ACTIVE = frozenset(('preflighting', 'running', 'dispatching', 'reconciling'))
ACK_CODES = frozenset(('ssl_panel_sync', 'legacy_panel_upgrade', 'php_update_restart', 'upload_overwrite'))


def now():
    return timezone.now().isoformat()


def bounded(value, maximum=MAX_JSON_BYTES):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('任务 JSON 格式无效') from None
    if len(encoded.encode('utf-8')) > maximum:
        raise ValueError('任务数据超出单次限制')
    return copy.deepcopy(value)


def preflight_summary(children):
    counts = {name: sum(child['state'] == name for child in children)
              for name in ('ready', 'already', 'skipped', 'failed')}
    return {**counts, 'total': len(children)}


def check_preflight_size(targets):
    # Count the stored child envelopes too, not only adapter items. Reserve the
    # zero summary of unfinished targets so later failures cannot exceed the cap.
    payload = [{'children': target.children, 'summary': target.summary or preflight_summary([])}
               for target in targets]
    try:
        bounded(payload)
    except ValueError:
        raise ValueError('整个任务预检快照超过 1 MiB，请缩小批次') from None


def permission(user, name):
    if not user.has_perms(['exec.panel.' + name]):
        raise PermissionError('权限拒绝')


def visible(user, queryset=None):
    queryset = queryset if queryset is not None else PanelTask.objects.all()
    return queryset if user.has_perms(['exec.panel.manage']) else queryset.filter(owner_id=user.pk)


def owned(user, task_id, action, lock=False):
    permission(user, action)
    qs = PanelTask.objects.select_for_update() if lock else PanelTask.objects
    try:
        return visible(user, qs).get(pk=positive_id(task_id))
    except PanelTask.DoesNotExist:
        raise PermissionError('任务不存在或无权访问') from None


def configured(panel, revision=None):
    if not panel.api_base_url or not panel.api_key_ciphertext or not panel.api_checked_at:
        raise ValueError('请先配置 API 并测试连接')
    if revision is not None and panel.api_config_revision != revision:
        raise ValueError('API 配置在预检后变化，请创建新预检')


def safe_panel(panel):
    return {'id': panel.pk, 'name': panel.name, 'group_id': panel.group_id,
            'group_name': panel.group.name, 'api_config_revision': panel.api_config_revision,
            'api_configured': bool(panel.api_base_url and panel.api_key_ciphertext),
            'api_checked': bool(panel.api_checked_at), 'api_version': panel.api_version}


def preflight_prerequisite_error(panel):
    """Local saved facts only: an empty-parameter target can never become runnable."""
    try:
        configured(panel)
    except ValueError as exc:
        return str(exc)
    if panel.api_version not in SUPPORTED_VERSIONS:
        return '面板版本未知或尚未经过协议适配；已适配 9.2.0 / 11.7.0 / 13.1.0，请重新测试连接'
    return ''


def selected_panels(data):
    panel_ids, group_ids = data.get('panel_ids', []), data.get('group_ids', [])
    if (not isinstance(panel_ids, list) or not isinstance(group_ids, list)
            or len(panel_ids) > MAX_PANELS or len(group_ids) > MAX_PANELS):
        raise ValueError('单次最多选择 50 个面板或分组')
    ids = {positive_id(v) for v in panel_ids}
    selected = {positive_id(v) for v in group_ids}
    groups = dict(PanelGroup.objects.values_list('id', 'parent_id'))
    if not selected.issubset(groups):
        raise ValueError('分组不存在')
    while True:
        expanded = selected | {gid for gid, parent in groups.items() if parent in selected}
        if expanded == selected:
            break
        selected = expanded
    if Panel.objects.filter(pk__in=ids).count() != len(ids):
        raise ValueError('面板不存在')
    ids.update(Panel.objects.filter(group_id__in=selected).values_list('id', flat=True))
    if not 1 <= len(ids) <= MAX_PANELS:
        raise ValueError('请选择 1 至 50 个面板')
    return list(Panel.objects.select_related('group').filter(pk__in=ids).order_by('id'))


def prepare(user, data, retry_parent=None):
    permission(user, 'create')
    bounded(data)
    strict(data, ('operation', 'panel_ids', 'group_ids', 'params', 'params_by_panel'), ('operation',))
    operation = get_operation(data['operation'])
    panels = selected_panels(data)
    if operation.code == 'file_upload':
        return prepare_upload(user, data, panels, operation, retry_parent)
    if ('params' in data) == ('params_by_panel' in data):
        raise ValueError('请提供公共参数或每个面板的参数映射之一')
    mapping = data.get('params_by_panel')
    ids = {str(p.pk) for p in panels}
    if mapping is not None and (not isinstance(mapping, dict) or set(mapping) != ids):
        raise ValueError('参数映射必须恰好包含每个选中面板')
    clean, blocked = {}, {}
    for panel in panels:
        params = copy.deepcopy(mapping[str(panel.pk)] if mapping is not None else data['params'])
        prerequisite = preflight_prerequisite_error(panel)
        if params == {} and prerequisite:
            clean[str(panel.pk)] = {}
            blocked[panel.pk] = prerequisite
        else:
            if params == {} and retry_parent is not None:
                raise ValueError('面板连接条件已变化，请重新填写各面板参数并创建新预检')
            clean[str(panel.pk)] = operation.validate(params)
    bounded(clean)
    with transaction.atomic():
        task = PanelTask.objects.create(operation=operation.code, owner=user, params_by_panel=clean,
                                        retry_parent=retry_parent,
                                        audit=[{'action': 'prepare', 'actor_id': user.pk, 'at': now()}])
        PanelTaskTarget.objects.bulk_create([
            PanelTaskTarget(task=task, panel=panel, panel_snapshot=safe_panel(panel), params=clean[str(panel.pk)],
                            **({'state': 'failed', 'message': blocked[panel.pk], 'summary': preflight_summary([])}
                               if panel.pk in blocked else {}))
            for panel in panels])
        refresh_task(task)
    return task


def prepare_upload(user, data, panels, operation, retry_parent=None):
    from . import upload_store as store
    from .models import PanelUploadBinding
    common, mapping = data.get('params', {}), data.get('params_by_panel', {})
    strict(common, ('bundle_id', 'target_dir'), ('bundle_id',))
    common_dir = common.get('target_dir', '')
    if not isinstance(common_dir, str):
        raise ValueError('统一目标目录无效')
    if common_dir.strip():
        store.absolute_directory(common_dir.strip())
    ids = {str(panel.pk) for panel in panels}
    if not isinstance(mapping, dict) or not set(mapping).issubset(ids):
        raise ValueError('单独参数包含未选择的面板')
    clean = {}
    for panel in panels:
        override = mapping.get(str(panel.pk), {})
        strict(override, ('target_dir',))
        individual = override.get('target_dir')
        try:
            directory = store.effective_directory(common_dir, individual)
        except ValueError:
            raise ValueError('面板“' + panel.name + '”的目标目录缺失或无效') from None
        clean[str(panel.pk)] = operation.validate({'bundle_id': common['bundle_id'], 'target_dir': directory,
            'directory_source': 'individual' if isinstance(individual, str) and individual.strip() else 'common'})
    with transaction.atomic():
        # Discard/cleanup also locks this row, so a sealed source cannot expire
        # between ownership validation and binding it to the durable task.
        bundle = store.owned_bundle(user, common['bundle_id'], sealed=True, lock=True)
        task = PanelTask.objects.create(operation=operation.code, owner=user, params_by_panel=bounded(clean),
            retry_parent=retry_parent, audit=[{'action': 'prepare', 'actor_id': user.pk, 'at': now()}])
        for panel in panels:
            target = PanelTaskTarget.objects.create(task=task, panel=panel, panel_snapshot=safe_panel(panel),
                                                   params=clean[str(panel.pk)])
            PanelUploadBinding.objects.create(target=target, bundle=bundle)
    return task


def required_acks(targets):
    codes = set()
    for target in targets:
        for child in target.children:
            if child['snapshot']['state'] == 'ready':
                values = child['snapshot'].get('plan', {}).get('required_acknowledgements', [])
                if not isinstance(values, list) or any(v not in ACK_CODES for v in values):
                    raise ValueError('冻结计划包含未知确认项')
                codes.update(values)
    return sorted(codes)


def refresh_task(task):
    """Caller holds the task row lock; every writer uses task -> target lock order."""
    states = list(task.targets.values_list('state', flat=True))
    if any(s == 'uncertain' for s in states):
        task.state = 'uncertain'
    elif any(s in ('remote', 'reconciling') for s in states):
        task.state = 'remote'
    elif any(s in ('running', 'dispatching', 'queued') for s in states):
        task.state = 'running'
    elif any(s in ('preflight_queued', 'preflighting') for s in states):
        task.state = 'preflight_queued'
    elif any(s == 'prepared' for s in states):
        task.state = 'prepared'
    elif states and all(s == 'cancelled' for s in states):
        task.state = 'cancelled'
    else:
        task.state = 'completed'
    task.save(update_fields=['state', 'updated_at'])


def settle_target(target):
    states = [c['state'] for c in target.children]
    if 'uncertain' in states:
        target.state = 'uncertain'
    elif 'remote' in states:
        target.state = 'remote'
    elif 'ready' in states:
        target.state = 'queued'
    elif states and all(s == 'cancelled' for s in states):
        target.state = 'cancelled'
    else:
        target.state = 'completed'
    if all(s in TERMINAL for s in states):
        PanelLock.objects.filter(target=target).delete()
    target.save(update_fields=['children', 'state', 'message', 'reconcile_requested', 'updated_at'])


def audit(task, user, action, **values):
    task.audit = bounded([*task.audit, {'action': action, 'actor_id': user.pk, 'at': now(), **values}])
    task.save(update_fields=['audit', 'updated_at'])


def start(user, task_id, data):
    strict(data, ('acknowledgements',), ('acknowledgements',))
    codes = data['acknowledgements']
    if not isinstance(codes, list) or len(codes) > len(ACK_CODES) or any(not isinstance(c, str) for c in codes):
        raise ValueError('确认项格式无效')
    with transaction.atomic():
        task = owned(user, task_id, 'execute', lock=True)
        if task.state != 'prepared' or task.cancel_requested:
            raise ValueError('任务尚未完成预检或已提交，请刷新')
        targets = list(task.targets.select_for_update().select_related('panel'))
        if sum(len(t.children) for t in targets) > MAX_CHILDREN:
            raise ValueError('单次最多处理 200 个子项')
        check_preflight_size(targets)
        if sorted(codes) != required_acks(targets):
            raise ValueError('请明确确认全部冻结计划要求的确认项')
        for target in targets:
            if target.state == 'prepared':
                configured(target.panel, target.panel_snapshot['api_config_revision'])
        task.acknowledgements = sorted(codes)
        task.save(update_fields=['acknowledgements', 'updated_at'])
        audit(task, user, 'start', acknowledgements=sorted(codes))
        for target in targets:
            if target.state == 'prepared':
                target.state = 'queued'
                target.save(update_fields=['state', 'updated_at'])
        refresh_task(task)
    return task


def cancel(user, task_id):
    with transaction.atomic():
        task = owned(user, task_id, 'cancel', lock=True)
        task.cancel_requested = True
        task.save(update_fields=['cancel_requested', 'updated_at'])
        for target in task.targets.select_for_update():
            if task.operation == 'file_upload':
                from . import upload_runner as uploads
                uploads.cancel_unsent(target)
                if target.state in ACTIVE:
                    target.summary = {**target.summary, 'upload': uploads.summary(target)}
                    target.save(update_fields=['summary', 'updated_at'])
                elif target.children:
                    uploads.sync(target)
                else:
                    target.state = 'cancelled'
                    target.save(update_fields=['state', 'updated_at'])
                continue
            for child in target.children:
                if child['state'] == 'ready' and not child['intents']:
                    child['state'] = 'cancelled'
                    child['result'] = {'state': 'cancelled', 'message': '已取消未发送子项', 'evidence': {}}
            # An active call owns its state until its result is saved.
            if target.state not in ACTIVE:
                if target.state in ('preflight_queued', 'prepared'):
                    target.state = 'cancelled'
                    target.save(update_fields=['state', 'children', 'updated_at'])
                else:
                    settle_target(target)
            else:
                target.save(update_fields=['children', 'updated_at'])
        audit(task, user, 'cancel', note='仅取消尚未发送子项；远端操作未声明停止')
        refresh_task(task)
    return task


def reconcile(user, task_id):
    with transaction.atomic():
        task = owned(user, task_id, 'view', lock=True)
        for target in task.targets.select_for_update().filter(state__in=('remote', 'uncertain')):
            target.reconcile_requested = True
            target.save(update_fields=['reconcile_requested', 'updated_at'])
        audit(task, user, 'reconcile')
    return task


def resolve(user, task_id, data):
    permission(user, 'manage')
    strict(data, ('target_id', 'child_key', 'outcome', 'reason', 'evidence'),
           ('target_id', 'child_key', 'outcome', 'reason', 'evidence'))
    if data['outcome'] not in ('success', 'failed'):
        raise ValueError('人工结论必须是 success 或 failed')
    for name in ('reason', 'evidence', 'child_key'):
        if not isinstance(data[name], str) or not data[name].strip() or len(data[name]) > 2000:
            raise ValueError('必须填写原因和核验依据')
    with transaction.atomic():
        task = PanelTask.objects.select_for_update().get(pk=positive_id(task_id))
        target = task.targets.select_for_update().get(pk=positive_id(data['target_id']))
        if target.state in ACTIVE:
            raise ValueError('调用仍在执行，不能人工覆盖')
        if task.operation == 'file_upload':
            from .upload_runner import resolve_file
            resolve_file(user, task, target, data)
            return task
        child = next((c for c in target.children if c['snapshot']['key'] == data['child_key']), None)
        if not child or child['state'] not in ('remote', 'uncertain'):
            raise ValueError('仅可处理待确认或不确定子项')
        resolution = {'actor_id': user.pk, 'at': now(), **{k: data[k] for k in ('outcome', 'reason', 'evidence')}}
        child.setdefault('resolutions', []).append(resolution)
        child['state'] = data['outcome']
        child['result'] = {'state': data['outcome'], 'message': '管理员依据人工核验作出结论',
                           'evidence': {'manual_resolution': resolution}}
        settle_target(target)
        audit(task, user, 'resolve', target_id=target.pk, child_key=data['child_key'], **resolution)
        refresh_task(task)
    return task


def retry(user, task_id):
    source = owned(user, task_id, 'create')
    if source.operation == 'file_upload':
        values = source.params_by_panel
        return prepare(user, {'operation': source.operation,
            'panel_ids': list(source.targets.values_list('panel_id', flat=True)),
            'params': {'bundle_id': next(iter(values.values()))['bundle_id'], 'target_dir': ''},
            'params_by_panel': {key: {'target_dir': value['target_dir']} for key, value in values.items()}},
            retry_parent=source)
    return prepare(user, {'operation': source.operation,
                         'panel_ids': list(source.targets.values_list('panel_id', flat=True)),
                         'params_by_panel': source.params_by_panel}, retry_parent=source)


# Adapter return values are trusted, selected metadata. Filter sensitive keys again at
# the API boundary; no model.to_dict() or raw transport response enters a task view.
SENSITIVE = re.compile(r'password|private.?key|ciphertext|access.?token|request.?token|api.?key|raw|pem|csr|tls_ca', re.I)


def safe_tree(value):
    if isinstance(value, dict):
        return {k: safe_tree(v) for k, v in value.items() if isinstance(k, str) and not SENSITIVE.search(k)}
    if isinstance(value, list):
        return [safe_tree(v) for v in value]
    return value if value is None or type(value) in (str, int, float, bool) else None


def task_view(task, detail=True):
    data = {'id': task.pk, 'operation': task.operation, 'owner_id': task.owner_id, 'state': task.state,
            'retry_parent_id': task.retry_parent_id, 'cancel_requested': task.cancel_requested,
            'acknowledgements': task.acknowledgements, 'created_at': task.created_at.isoformat(),
            'updated_at': task.updated_at.isoformat()}
    if detail:
        targets = list(task.targets.all())
        data.update(required_acknowledgements=required_acks(targets), audit=task.audit,
                    targets=[{'id': t.pk, 'panel': t.panel_snapshot, 'state': t.state,
                              'message': t.message, 'summary': t.summary,
                              'children': t.children} for t in targets])
    return safe_tree(data)
