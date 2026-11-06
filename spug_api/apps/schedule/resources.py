"""Owner and complete host scope for legacy script schedules and queued jobs."""
import json
from django.db import transaction

from apps.account import access
from .models import Task, History


def targets(user, values, *, manage=False, monitor=False):
    user = access.current_user(user)
    if not isinstance(values, list) or not values:
        raise ValueError('请选择执行对象')
    ids = []
    for value in values:
        if value == 'local':
            access.admin(user)
        elif value == 'monitor' and monitor:
            continue
        elif type(value) is int and value > 0:
            ids.append(value)
        elif isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0:
            ids.append(int(value))
        else:
            raise ValueError('执行对象无效')
    access.require_resources(user, 'host', ids, manage=manage)
    return values


def definition_scope(user, task, *, manage=False):
    targets(user, json.loads(task.targets), manage=manage, monitor=task.trigger == 'monitor')
    monitor_hosts = set()
    if task.trigger == 'monitor':
        from apps.monitor.models import Detection
        from apps.monitor import resources as detections
        links = access.identifiers(json.loads(task.trigger_args))
        records = list(Detection.objects.filter(pk__in=links, is_active=True))
        if not links or len(records) != len(links):
            raise ValueError('关联监控项不存在或已停止')
        for record in records:
            if record.created_by_id != task.created_by_id:
                raise PermissionError('关联监控项与计划创建者不一致')
            detections.authorize(user, record, manage=manage,
                                 code='monitor.monitor.edit' if manage else 'monitor.monitor.view')
            monitor_hosts.update(detections.host_ids(record))
    return monitor_hosts


def authorized(user, task, action='view', *, active=False, resolved=None):
    user = access.require(user, 'schedule.schedule.' + action)
    task = Task.objects.get(pk=getattr(task, 'pk', task))
    if not user.is_supper and task.created_by_id != user.pk:
        access.reject(user, '计划不存在或无权访问')
    if task.interpreter not in ('sh', 'python') or active and not task.is_active:
        raise ValueError('计划已停止或执行类型已变化')
    selected = json.loads(task.targets)
    monitor_hosts = definition_scope(user, task, manage=action != 'view')
    if resolved is not None:
        targets(user, resolved, manage=action != 'view')
        if task.trigger == 'monitor' and 'monitor' in selected:
            static = {str(x) for x in selected if x != 'monitor'}
            actual = {str(x) for x in resolved}
            if not static.issubset(actual) or not actual - static or not (actual - static).issubset({str(x) for x in monitor_hosts}):
                raise ValueError('执行目标不属于关联监控项')
        elif {str(x) for x in resolved} != {str(x) for x in selected}:
            raise ValueError('计划执行目标已变化')
    return task


def visible(user):
    current = access.require(user, 'schedule.schedule.view')
    ids = []
    for task in Task.objects.all() if current.is_supper else Task.objects.filter(created_by=current):
        try:
            if task.interpreter == 'baota':
                from . import panel_tasks
                panel_tasks.access(current, task)
            else:
                authorized(current, task)
            ids.append(task.pk)
        except (ValueError, PermissionError):
            continue
    return Task.objects.filter(pk__in=ids)


def runtime(task_id, interpreter, command, selected, *, active=True):
    task = Task.objects.get(pk=task_id)
    task = authorized(task.created_by_id, task, 'run', active=active, resolved=selected)
    if task.interpreter != interpreter or task.command != command:
        raise ValueError('计划内容已变化，未发送命令')
    return task


def runtime_for_history(history_id, task_id, host_id, interpreter, command, *, active=True):
    # Manual and queued workers share the same durable terminal-state guard.
    latest = History.objects.filter(pk=history_id, task_id=task_id, status=0).first()
    if latest is None:
        raise PermissionError('计划执行记录已结束或任务绑定已变化，未继续发送')
    output = json.loads(latest.output)
    if not isinstance(output, dict) or str(host_id) not in output:
        raise ValueError('计划执行记录目标已变化，未继续发送')
    return runtime(task_id, interpreter, command, list(output), active=active)


def save_history_result(history_id, task_id, host_id, result):
    with transaction.atomic():
        history = History.objects.select_for_update().filter(pk=history_id, task_id=task_id, status=0).first()
        if history is None:
            return False
        output = json.loads(history.output)
        if str(host_id) not in output:
            raise ValueError('计划执行记录目标已变化')
        output[str(host_id)] = result
        history.output = json.dumps(output)
        if all(output.values()):
            history.status = 1 if all(value[0] == 0 for value in output.values()) else 2
        history.save(update_fields=['output', 'status'])
        return True


def history_access(user, record, *, manage=False):
    record = History.objects.get(pk=getattr(record, 'pk', record))
    authorized(user, record.task_id, 'run' if manage else 'view')
    outputs = json.loads(record.output)
    if not isinstance(outputs, dict):
        raise ValueError('执行历史目标格式无效')
    if outputs:
        targets(user, list(outputs), manage=manage)
    return record


def fail_history(history_id, message):
    """Close unsent work after revocation/config changes, preserving results."""
    with transaction.atomic():
        history = History.objects.select_for_update().filter(pk=history_id, status=0).first()
        if history is None:
            return
        output = json.loads(history.output)
        history.output = json.dumps({key: value or [1, 0, message] for key, value in output.items()})
        history.status = 2
        history.save(update_fields=['output', 'status'])
