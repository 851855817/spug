import copy
import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction

from apps.host.models import Group, Host
from apps.account import access
from . import catalogue
from .models import OpsAudit, OpsBaseline, OpsRun, OpsTarget


STATES = ('pending', 'running', 'success', 'failed', 'unknown', 'cancelled')
CONTINUOUS_BATCH_SIZE = 50
CONTINUOUS_FAILURE_THRESHOLD = 51


def is_continuous(run):
    return (run.batch_size, run.failure_threshold) == (CONTINUOUS_BATCH_SIZE, CONTINUOUS_FAILURE_THRESHOLD)


def timestamp(value):
    return value.isoformat(timespec='seconds') if value else None


def require_admin(user, action='view'):
    return access.require(user, 'ops.job.' + action)


def run_host_ids(run):
    targets = list(run.targets.values_list('host_id', 'host_snapshot'))
    if not targets:
        raise ValueError('作业目标清单无效')
    ids = [pk or snapshot.get('id') for pk, snapshot in targets]
    return access.identifiers(ids)


def authorized_run(user, run, action='view', manage=False):
    current = require_admin(user, action)
    if not current.is_supper and run.created_by_id != current.pk:
        access.reject(current, '作业不存在或无权访问')
    if not current.is_supper or manage:
        ids = run_host_ids(run)
        if current.is_supper:
            ids = set(Host.objects.filter(pk__in=ids).values_list('pk', flat=True))
        access.require_resources(current, 'host', list(ids), manage=manage)
    return run


def visible_runs(user):
    current = require_admin(user)
    qs = OpsRun.objects.all()
    if current.is_supper:
        return qs
    allowed = access.allowed_ids(current, 'host')
    visible = []
    for run in qs.filter(created_by=current).prefetch_related('targets'):
        try:
            ids = run_host_ids(run)
            if ids.issubset(allowed):
                visible.append(run.pk)
        except ValueError:
            continue
    return qs.filter(pk__in=visible)


def visible_baselines(user):
    current = require_admin(user)
    qs = OpsBaseline.objects.all()
    if current.is_supper:
        return qs
    return qs.filter(created_by=current, target__run_id__in=visible_runs(current).values('pk'))


def positive(value, label='编号'):
    if type(value) is not int or value <= 0:
        raise ValueError('%s必须为正整数' % label)
    return value


def id_list(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError('目标列表无效')
    return {positive(x) for x in value}


def identity(host):
    # Key changes invalidate queued work without retaining credentials in the run.
    payload = json.dumps([host.id, host.hostname, host.port or 22, host.username, host.private_key],
                         ensure_ascii=True, separators=(',', ':')).encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def host_view(host, user=None):
    return {'id': host.id, 'name': host.name, 'hostname': host.hostname,
            'group_ids': sorted(group.id for group in host.groups.all()
                                if user is None or group.id in access.group_ids(user, 'host'))}


def audit(run, event, user=None, detail=None):
    OpsAudit.objects.create(run=run, event=event, actor_id=user.id if user else None, detail=detail or {})


@transaction.atomic
def create_run(user, data):
    user = require_admin(user, 'create')
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        raise ValueError('请确认只读检查范围后创建作业')
    if set(data) - {'job', 'host_ids', 'group_ids', 'params', 'batch_size', 'failure_threshold', 'confirmed', 'execution_mode'}:
        raise ValueError('不支持的作业字段')
    job = catalogue.get_job(data.get('job'))
    params = catalogue.parameters(job['code'], data.get('params', {}))
    mode = data.get('execution_mode', 'manual')
    if mode not in ('manual', 'continuous'):
        raise ValueError('不支持的执行方式')
    if mode == 'continuous':
        if {'batch_size', 'failure_threshold'} & data.keys():
            raise ValueError('连续巡检不接受手动批次参数')
        batch_size, failure_threshold = CONTINUOUS_BATCH_SIZE, CONTINUOUS_FAILURE_THRESHOLD
    else:
        batch_size = positive(data.get('batch_size', 1), '批次大小')
        failure_threshold = positive(data.get('failure_threshold', 1), '失败阈值')
        if batch_size > 10 or failure_threshold > batch_size:
            raise ValueError('批次大小为 1–10，失败阈值不能超过批次大小')
    selected = id_list(data.get('host_ids', []))
    selected_groups = id_list(data.get('group_ids', []))
    access.require_groups(user, 'host', list(selected_groups))
    groups = {x.id: x for x in Group.objects.all()}
    if selected_groups - groups.keys():
        raise ValueError('主机分组不存在，请刷新后重试')
    frontier = set(selected_groups)
    while frontier:
        children = {x.id for x in groups.values() if x.parent_id in frontier} - selected_groups
        selected_groups.update(children)
        frontier = children
    if selected_groups:
        selected.update(access.scope(user, 'host').filter(groups__id__in=selected_groups).values_list('id', flat=True))
    if not selected or len(selected) > 50:
        raise ValueError('每次作业必须选择 1–50 台主机')
    access.require_resources(user, 'host', list(selected), manage=True)
    hosts = list(Host.objects.select_for_update().filter(id__in=selected).prefetch_related('groups').order_by('id'))
    if len(hosts) != len(selected):
        raise ValueError('主机不存在，请刷新后重试')
    run = OpsRun.objects.create(job=job['code'], job_name=job['name'], version=job['version'], params=params,
                                batch_size=batch_size, failure_threshold=failure_threshold, created_by=user)
    OpsTarget.objects.bulk_create([
        OpsTarget(run=run, host=host, position=i, host_snapshot=host_view(host), identity_digest=identity(host))
        for i, host in enumerate(hosts)
    ])
    audit(run, 'created', user, {'targets': len(hosts), 'batch_size': batch_size,
                                 'failure_threshold': failure_threshold, 'confirmed': True, 'execution_mode': mode})
    return run


def target_view(target):
    return {'id': target.id, 'host': target.host_snapshot, 'state': target.state, 'category': target.category,
            'message': target.message, 'checks': target.checks,
            'started_at': timestamp(target.started_at), 'finished_at': timestamp(target.finished_at)}


def scoped_snapshot(snapshot, user=None):
    result = copy.deepcopy(snapshot)
    if user is not None:
        groups = access.group_ids(user, 'host')
        result['group_ids'] = [pk for pk in result.get('group_ids', []) if pk in groups]
    return result


def run_view(run, detail=True, user=None):
    if user is not None:
        authorized_run(user, run)
    targets = list(run.targets.all())
    counts = {state: sum(x.state == state for x in targets) for state in STATES}
    counts['total'] = len(targets)
    counts['warning_checks'] = sum(check.get('status') == 'warning' for target in targets for check in target.checks)
    counts['unknown_checks'] = sum(check.get('status') == 'unknown' for target in targets for check in target.checks)
    response = {'id': run.id, 'job': run.job, 'job_name': run.job_name, 'version': run.version,
                'params': run.params, 'state': run.state, 'execution_mode': 'continuous' if is_continuous(run) else 'manual', 'batch_size': run.batch_size,
                'failure_threshold': run.failure_threshold, 'batch_done': run.batch_done,
                'batch_failures': run.batch_failures, 'cancel_requested': run.cancel_requested,
                'counts': counts, 'created_at': timestamp(run.created_at), 'updated_at': timestamp(run.updated_at),
                'created_by': run.created_by_id}
    if detail:
        response['targets'] = [target_view(x) for x in targets]
        if user is not None:
            for target in response['targets']:
                target['host'] = scoped_snapshot(target['host'], user)
        response['audit'] = [{'id': x.id, 'event': x.event, 'actor_id': x.actor_id,
                              'created_at': timestamp(x.created_at), 'detail': x.detail} for x in run.audit.all()]
    return response


@transaction.atomic
def continue_run(user, run_id, data):
    run = OpsRun.objects.select_for_update().get(pk=positive(run_id))
    authorized_run(user, run, 'continue', manage=True)
    if run.cancel_requested or run.state not in ('paused', 'tripped'):
        raise ValueError('仅暂停或熔断作业可继续')
    if run.state == 'tripped' and data.get('acknowledge') is not True:
        raise ValueError('请明确确认失败或待核实结果后继续')
    previous = run.state
    run.state = 'queued' if run.targets.filter(state='pending').exists() else 'completed'
    run.batch_done = run.batch_failures = 0
    run.save(update_fields=['state', 'batch_done', 'batch_failures', 'updated_at'])
    audit(run, 'continue', user, {'previous_state': previous, 'acknowledged': data.get('acknowledge') is True})
    return run


@transaction.atomic
def cancel_run(user, run_id):
    run = OpsRun.objects.select_for_update().get(pk=positive(run_id))
    authorized_run(user, run, 'cancel', manage=True)
    if run.state in ('completed', 'cancelled'):
        raise ValueError('作业已结束')
    run.cancel_requested = True
    run.state = 'cancelled'
    run.save(update_fields=['cancel_requested', 'state', 'updated_at'])
    run.targets.filter(state='pending').update(state='cancelled', message='已取消，未下发检查')
    audit(run, 'cancel', user, {'inflight_may_continue': run.targets.filter(state='running').exists()})
    return run


def baseline_view(baseline, user=None):
    if user is not None and not visible_baselines(user).filter(pk=baseline.pk).exists():
        access.reject(user, '基线不存在或无权访问')
    return {'id': baseline.id, 'name': baseline.name, 'job': baseline.job, 'version': baseline.version,
            'params': baseline.params, 'host': scoped_snapshot(baseline.host_snapshot, user), 'checks': baseline.checks,
            'created_at': timestamp(baseline.created_at), 'run_id': baseline.target.run_id, 'target_id': baseline.target_id}


@transaction.atomic
def create_baseline(user, data):
    run = OpsRun.objects.select_for_update().get(pk=positive(data.get('run_id')))
    authorized_run(user, run, 'baseline', manage=True)
    target = run.targets.select_for_update().get(pk=positive(data.get('target_id')))
    name = data.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError('基线名称长度为 1–100')
    if target.state != 'success' or not any(x['status'] != 'unknown' for x in target.checks):
        raise ValueError('仅成功采集的目标可创建基线')
    baseline = OpsBaseline.objects.create(name=name.strip(), job=run.job, version=run.version,
                                          params=copy.deepcopy(run.params),
                                          host_snapshot=copy.deepcopy(target.host_snapshot),
                                          checks=copy.deepcopy(target.checks), target=target, created_by=user)
    audit(run, 'baseline_created', user, {'baseline_id': baseline.id, 'target_id': target.id})
    return baseline


def diff_run(run, baseline_id, user=None):
    if user is not None:
        authorized_run(user, run)
        if not visible_baselines(user).filter(pk=baseline_id).exists():
            access.reject(user, '基线不存在或无权访问')
    baseline = OpsBaseline.objects.select_related('target').get(pk=positive(baseline_id))
    if (run.job, run.version, run.params) != (baseline.job, baseline.version, baseline.params):
        raise ValueError('只能比较相同作业、代码版本及参数的基线')
    before = {x['key']: x for x in baseline.checks}
    items = []
    for target in run.targets.all():
        after = {x['key']: x for x in target.checks}
        changes = []
        for key, label, *_ in catalogue.SCHEMAS[run.job]:
            old, new = before.get(key), after.get(key)
            old_known = old and old.get('status') != 'unknown'
            new_known = new and new.get('status') != 'unknown' and target.state == 'success'
            if target.state != 'success' or not old_known:
                state = 'unknown'
            elif not new_known:
                state = 'missing'
            else:
                state = 'same' if old['value'] == new['value'] else 'changed'
            changes.append({'key': key, 'label': label, 'before': old['value'] if old_known else None,
                            'after': new['value'] if new_known else None, 'state': state})
        items.append({'host': scoped_snapshot(target.host_snapshot, user), 'state': target.state, 'changes': changes})
    return {'baseline': baseline_view(baseline, user=user), 'items': items}
