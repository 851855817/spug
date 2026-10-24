import copy
import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction

from apps.host.models import Group, Host
from . import catalogue
from .models import OpsAudit, OpsBaseline, OpsRun, OpsTarget


STATES = ('pending', 'running', 'success', 'failed', 'unknown', 'cancelled')


def timestamp(value):
    return value.isoformat(timespec='seconds') if value else None


def require_admin(user):
    if not user or not user.is_supper or not user.is_active or user.is_deleted:
        raise ValueError('仅超级管理员可使用运维作业')


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


def host_view(host):
    return {'id': host.id, 'name': host.name, 'hostname': host.hostname,
            'group_ids': sorted(group.id for group in host.groups.all())}


def audit(run, event, user=None, detail=None):
    OpsAudit.objects.create(run=run, event=event, actor_id=user.id if user else None, detail=detail or {})


@transaction.atomic
def create_run(user, data):
    require_admin(user)
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        raise ValueError('请确认只读检查范围后创建作业')
    if set(data) - {'job', 'host_ids', 'group_ids', 'params', 'batch_size', 'failure_threshold', 'confirmed'}:
        raise ValueError('不支持的作业字段')
    job = catalogue.get_job(data.get('job'))
    params = catalogue.parameters(job['code'], data.get('params', {}))
    batch_size = positive(data.get('batch_size', 1), '批次大小')
    failure_threshold = positive(data.get('failure_threshold', 1), '失败阈值')
    if batch_size > 10 or failure_threshold > batch_size:
        raise ValueError('批次大小为 1–10，失败阈值不能超过批次大小')
    selected = id_list(data.get('host_ids', []))
    selected_groups = id_list(data.get('group_ids', []))
    groups = {x.id: x for x in Group.objects.all()}
    if selected_groups - groups.keys():
        raise ValueError('主机分组不存在，请刷新后重试')
    frontier = set(selected_groups)
    while frontier:
        children = {x.id for x in groups.values() if x.parent_id in frontier} - selected_groups
        selected_groups.update(children)
        frontier = children
    if selected_groups:
        selected.update(Host.objects.filter(groups__id__in=selected_groups).values_list('id', flat=True))
    if not selected or len(selected) > 50:
        raise ValueError('每次作业必须选择 1–50 台主机')
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
                                 'failure_threshold': failure_threshold, 'confirmed': True})
    return run


def target_view(target):
    return {'id': target.id, 'host': target.host_snapshot, 'state': target.state, 'category': target.category,
            'message': target.message, 'checks': target.checks,
            'started_at': timestamp(target.started_at), 'finished_at': timestamp(target.finished_at)}


def run_view(run, detail=True):
    targets = list(run.targets.all())
    counts = {state: sum(x.state == state for x in targets) for state in STATES}
    counts['total'] = len(targets)
    response = {'id': run.id, 'job': run.job, 'job_name': run.job_name, 'version': run.version,
                'params': run.params, 'state': run.state, 'batch_size': run.batch_size,
                'failure_threshold': run.failure_threshold, 'batch_done': run.batch_done,
                'batch_failures': run.batch_failures, 'cancel_requested': run.cancel_requested,
                'counts': counts, 'created_at': timestamp(run.created_at), 'updated_at': timestamp(run.updated_at),
                'created_by': run.created_by_id}
    if detail:
        response['targets'] = [target_view(x) for x in targets]
        response['audit'] = [{'id': x.id, 'event': x.event, 'actor_id': x.actor_id,
                              'created_at': timestamp(x.created_at), 'detail': x.detail} for x in run.audit.all()]
    return response


@transaction.atomic
def continue_run(user, run_id, data):
    require_admin(user)
    run = OpsRun.objects.select_for_update().get(pk=positive(run_id))
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
    require_admin(user)
    run = OpsRun.objects.select_for_update().get(pk=positive(run_id))
    if run.state in ('completed', 'cancelled'):
        raise ValueError('作业已结束')
    run.cancel_requested = True
    run.state = 'cancelled'
    run.save(update_fields=['cancel_requested', 'state', 'updated_at'])
    run.targets.filter(state='pending').update(state='cancelled', message='已取消，未下发检查')
    audit(run, 'cancel', user, {'inflight_may_continue': run.targets.filter(state='running').exists()})
    return run


def baseline_view(baseline):
    return {'id': baseline.id, 'name': baseline.name, 'job': baseline.job, 'version': baseline.version,
            'params': baseline.params, 'host': baseline.host_snapshot, 'checks': baseline.checks,
            'created_at': timestamp(baseline.created_at), 'run_id': baseline.target.run_id, 'target_id': baseline.target_id}


@transaction.atomic
def create_baseline(user, data):
    require_admin(user)
    run = OpsRun.objects.select_for_update().get(pk=positive(data.get('run_id')))
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


def diff_run(run, baseline_id):
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
        items.append({'host': target.host_snapshot, 'state': target.state, 'changes': changes})
    return {'baseline': baseline_view(baseline), 'items': items}
