"""Versioned schedule definitions and durable, local-only panel coordination.

Never performs HTTP/SSH. Network work stays in the existing panel worker. Task
and History rows form the durable outbox; creating a run and linking it commit
together. Lock order is schedule -> history -> panel task -> panel targets.
"""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone

from apps.account.models import User
from apps.account import access as resource_access
from apps.baota import task_service as s
from apps.baota.models import PanelTask, PanelUploadBinding, PanelUploadBundle
from apps.baota.operations import get_operation
from apps.baota.validation import positive_id
from libs import human_datetime
from .models import Task, History


KIND = 'baota'
MIN_INTERVAL = 300
ACK_LABELS = {
    'ssl_panel_sync': '证书申请或续签可能同步面板内同域名证书并重载 Web 服务，范围包括未选择的网站和面板 TLS。',
    'legacy_panel_upgrade': '按官方稳定通道升级，实际版本由宝塔决定，升级期间面板可能断开。',
    'php_update_restart': '按官方脚本更新当前 PHP 分支，可能重启 PHP 并短暂影响相关网站。',
    'upload_overwrite': '上传到固定目标目录，覆盖同名文件；源文件在删除计划前持续占用暂存配额。',
}


def encode(value):
    return json.dumps(s.bounded(value), ensure_ascii=False, allow_nan=False)


def decode(value):
    try:
        result = json.loads(value)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('面板计划数据无效，请重新配置') from None
    if not isinstance(result, dict):
        raise ValueError('面板计划数据无效，请重新配置')
    return result


def permitted(user, *codes):
    """Read current roles directly: authorization must not use the login cache."""
    try:
        current, granted = resource_access.permissions(user)
    except PermissionError:
        return False
    return current.is_supper or set(codes).issubset(granted)


def require(user, *codes):
    return resource_access.require(user, *codes)


def access(user, task, action='view'):
    user = require(user, 'schedule.schedule.' + action, 'exec.panel.view')
    task = Task.objects.get(pk=task.pk)
    if task.created_by_id != user.pk and not user.is_supper:
        raise PermissionError('权限拒绝或任务不存在')
    value = definition(task)
    resource_access.require_resources(user, 'panel', value['panel_ids'], manage=action != 'view')
    if action != 'view':
        s.action_permission(user, value['operation'])
    return task


def definition(task):
    value = decode(task.command)
    required = {'version', 'revision', 'template_id', 'template_token', 'operation', 'operation_label',
                'panel_ids', 'panels', 'params_by_panel', 'scope', 'revisions', 'required_codes',
                'acknowledgements', 'authorized_by'}
    if required - set(value) or set(value) - required - {'bundle_id', 'manifest'}:
        raise ValueError('面板计划数据不完整，请重新配置')
    if (task.interpreter != KIND or value.get('version') != 1
            or not isinstance(value.get('revision'), str)
            or not isinstance(value.get('panel_ids'), list)
            or not 1 <= len(value['panel_ids']) <= s.MAX_PANELS):
        raise ValueError('面板计划版本无效，请重新配置')
    keys = {str(positive_id(pid)) for pid in value['panel_ids']}
    if (len(keys) != len(value['panel_ids']) or any(not isinstance(value[name], dict) or set(value[name]) != keys
          for name in ('params_by_panel', 'scope', 'revisions'))
            or not isinstance(value['acknowledgements'], list)
            or any(code not in ACK_LABELS for code in value['acknowledgements'])):
        raise ValueError('面板计划范围或确认项无效，请重新配置')
    return value


def projection(item):
    # Directory provenance is presentation metadata; the absolute path is the
    # fixed upload destination even when common settings replay as overrides.
    value = {'key': item['key'], 'target': {k: v for k, v in item['target'].items() if k != 'directory_source'}}
    identity = item.get('before', {}).get('identity')
    if identity:
        value['identity'] = {k: identity.get(k) for k in ('id', 'name', 'path', 'project_type')}
    return value


def frozen(source):
    if any(a.get('action') == 'ssl_monitor' for a in source.audit):
        raise ValueError('SSL 监控派生任务不能转换为普通计划，请在 SSL 监控管理续签来源')
    if source.state == 'preflight_queued':
        raise ValueError('预检尚未完成，请稍后刷新')
    if source.state not in ('prepared', 'completed') or source.cancel_requested:
        raise ValueError('请选用已完成预检且没有在途操作的任务')
    targets = list(source.targets.select_related('panel__group').order_by('panel_id'))
    if not 1 <= len(targets) <= s.MAX_PANELS or sum(len(t.children) for t in targets) > s.MAX_CHILDREN:
        raise ValueError('模板目标数量无效')
    params, scope, panels, codes, revisions = {}, {}, [], set(), {}
    for target in targets:
        s.configured(target.panel, target.panel_snapshot['api_config_revision'])
        if (not target.children or target.state not in ('prepared', 'completed')
                or target.summary.get('upload', {}).get('failed')
                or any(c['snapshot'].get('state') not in ('ready', 'already', 'skipped') for c in target.children)):
            raise ValueError('预检存在失败或缺失项目，请修正后创建新预检')
        pid = str(target.panel_id)
        clean = copy.deepcopy(target.params)
        if clean.pop('all_sites', False):
            clean['sites'] = [c['snapshot']['target']['id'] for c in target.children]
        # Domain selection must not grow on later runs when a site gains aliases.
        if source.operation in ('ssl_apply', 'ssl_renew'):
            domains = copy.deepcopy(clean.get('domains', {}))
            for child in target.children:
                sid = str(child['snapshot']['target']['id'])
                selected = child['snapshot']['plan'].get('domains') or domains.get(sid)
                if not selected:
                    raise ValueError('部分网站未能核定证书域名，请明确选择域名或缩小计划范围')
                domains[sid] = selected
            clean['domains'] = domains
        params[pid] = clean
        scope[pid] = [projection(c['snapshot']) for c in target.children]
        panels.append({'id': target.panel_id, 'name': target.panel.name})
        revisions[pid] = target.panel.api_config_revision
        for child in target.children:
            values = child['snapshot'].get('plan', {}).get('required_acknowledgements', [])
            if not isinstance(values, list) or any(v not in s.ACK_CODES for v in values):
                raise ValueError('预检包含未知确认项')
            codes.update(values)
    value = dict(template_id=source.pk, operation=source.operation,
                 operation_label=get_operation(source.operation).label,
                 panel_ids=[p['id'] for p in panels], panels=panels,
                 params_by_panel=params, scope=scope, revisions=revisions, required_codes=sorted(codes))
    if source.operation == 'file_upload':
        bundles = set(source.targets.values_list('upload_binding__bundle_id', flat=True))
        if len(bundles) != 1 or None in bundles:
            raise ValueError('上传模板缺少固定源文件')
        value['bundle_id'] = bundles.pop()
        bundle = PanelUploadBundle.objects.get(pk=value['bundle_id'])
        if bundle.state != 'sealed':
            raise ValueError('源文件包不可用，请重新上传')
        value['manifest'] = bundle.manifest_sha256
    value['template_token'] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return s.bounded(value)


def public_plan(value):
    return {'template_id': value['template_id'], 'template_token': value['template_token'],
            'operation': value['operation'], 'operation_label': value['operation_label'],
            'panels': value['panels'], 'scope': [
                {'panel_id': p['id'], 'name': p['name'], 'items': [
                    str(i['target'].get('name') or i['target'].get('target_dir') or i['key'])
                    for i in value['scope'][str(p['id'])]]} for p in value['panels']],
            'required_acknowledgements': [{'code': code, 'label': ACK_LABELS[code]} for code in value['required_codes']],
            'acknowledgements': value.get('acknowledgements', [])}


def template(user, source_id):
    require(user, 'exec.panel.view', 'exec.panel.create')
    if not (permitted(user, 'schedule.schedule.add') or permitted(user, 'schedule.schedule.edit')):
        raise PermissionError('权限拒绝')
    source = visible_sources(user).filter(pk=positive_id(source_id)).first()
    if not source:
        raise PermissionError('权限拒绝或任务不存在')
    s.task_access(user, source, 'create')
    return public_plan(frozen(source))


def templates(user):
    require(user, 'exec.panel.view', 'exec.panel.create')
    if not (permitted(user, 'schedule.schedule.add') or permitted(user, 'schedule.schedule.edit')):
        raise PermissionError('权限拒绝')
    items = []
    for source in visible_sources(user).filter(state__in=('prepared', 'completed'), cancel_requested=False)[:100]:
        try:
            s.task_access(user, source, 'create')
            items.append(public_plan(frozen(source)))
        except (ValueError, PermissionError):
            continue
    return {'items': items}


def visible_sources(user, queryset=None):
    queryset = PanelTask.objects.all() if queryset is None else queryset
    return s.visible(user, queryset)


def validate_trigger(data):
    from .scheduler import Scheduler
    trigger, args = data.get('trigger'), data.get('trigger_args')
    if trigger not in ('date', 'interval', 'cron'):
        raise ValueError('面板任务只支持一次性、间隔或 Cron')
    if trigger == 'interval':
        if isinstance(args, bool) or not str(args).isdigit() or int(args) < MIN_INTERVAL:
            raise ValueError('面板任务间隔至少 300 秒')
        args = str(args)
    try:
        parsed = Scheduler.parse_trigger(trigger, args)
        current = datetime.now(datetime_timezone.utc)
        if parsed.get_next_fire_time(None, current) is None:
            raise ValueError()
        if trigger == 'date' and parsed.run_date <= current:
            raise ValueError()
    except (TypeError, ValueError, KeyError, OverflowError):
        raise ValueError('触发规则无效或已过期') from None
    if not isinstance(args, str) or len(args) > 255:
        raise ValueError('触发规则无效')
    return trigger, args


def unresolved(task):
    record = task.latest
    if not record:
        return False
    value = decode(record.output)
    if value.get('kind') != KIND:
        return record.status == 0
    panel_task = PanelTask.objects.filter(pk=value.get('panel_task_id')).first()
    return record.status == 0 or (panel_task is not None and panel_task.state not in ('completed', 'cancelled'))


def save(user, data):
    data = s.bounded(data)
    task_id = data.get('id')
    require(user, 'schedule.schedule.' + ('edit' if task_id else 'add'), 'exec.panel.view', 'exec.panel.create')
    plan = data.get('panel_plan')
    s.strict(plan, ('template_id', 'template_token', 'acknowledgements'),
             ('template_id', 'template_token', 'acknowledgements'))
    trigger, args = validate_trigger(data)
    for key, maximum in (('name', 50), ('type', 50), ('desc', 255)):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > maximum or (key != 'desc' and not value.strip()):
            raise ValueError('任务名称、类型或备注无效')
    notify = data.get('rst_notify', {'mode': '0'})
    if not isinstance(notify, dict) or len(encode(notify)) > 255:
        raise ValueError('失败通知配置无效')
    with transaction.atomic():
        current = Task.objects.select_for_update().filter(pk=task_id).first() if task_id else None
        if task_id:
            if current is None:
                raise PermissionError('权限拒绝或任务不存在')
            if current.interpreter != KIND:
                raise ValueError('请新建独立面板计划，不能转换脚本任务')
            access(user, current, 'edit')
            if current.is_active or unresolved(current):
                raise ValueError('请先停止计划并核实未结束的运行')
        source = visible_sources(user, PanelTask.objects.select_for_update()).filter(pk=positive_id(plan['template_id'])).first()
        if not source:
            raise PermissionError('权限拒绝或任务不存在')
        s.task_access(user, source, 'create')
        if current:
            s.task_access(current.created_by_id, source, 'create')
        value = frozen(source)
        if plan['template_token'] != value['template_token']:
            raise ValueError('预检模板已变化，请重新选择')
        if (not isinstance(plan['acknowledgements'], list)
                or sorted(plan['acknowledgements']) != value['required_codes']):
            raise ValueError('请确认全部面板操作影响')
        value.update(version=1, revision=uuid.uuid4().hex, authorized_by=None,
                     acknowledgements=value['required_codes'])
        if value.get('bundle_id'):
            bundle = PanelUploadBundle.objects.select_for_update().get(pk=value['bundle_id'])
            if bundle.state != 'sealed' or bundle.manifest_sha256 != value['manifest']:
                raise ValueError('源文件包不可用')
            if bundle.owner_id != user.pk:
                raise ValueError('上传计划须由源文件所属账号创建和启用')
        fields = dict(name=data['name'].strip(), type=data['type'].strip(), desc=data.get('desc', ''),
                      # Legacy consumers interpret targets as Host IDs. Panel
                      # IDs live exclusively in the typed definition namespace.
                      interpreter=KIND, command=encode(value), targets='[]',
                      trigger=trigger, trigger_args=args, rst_notify=encode(notify))
        if current:
            for key, value in fields.items():
                setattr(current, key, value)
            current.updated_by, current.updated_at = user, human_datetime()
            current.save()
        else:
            current = Task.objects.create(created_by=user, **fields)
    return current


def activate(user, task_id, enabled, confirmed=False):
    with transaction.atomic():
        task = Task.objects.select_for_update().get(pk=positive_id(task_id))
        access(user, task, 'enable')
        if type(enabled) is not bool:
            raise ValueError('启用状态无效')
        value = definition(task)
        if enabled:
            require(user, 'exec.panel.create', 'exec.panel.execute')
            creator = require(task.created_by_id, 'schedule.schedule.run', 'exec.panel.view', 'exec.panel.create', 'exec.panel.execute')
            s.action_permission(creator, value['operation'])
            resource_access.require_resources(creator, 'panel', value['panel_ids'], manage=True)
            if confirmed is not True:
                raise ValueError('请确认固定目标及到点自动执行')
            if unresolved(task):
                raise ValueError('上次运行尚未结束，请先核验结果')
            source = visible_sources(creator).filter(pk=value['template_id']).first()
            if source is None or frozen(source)['template_token'] != value['template_token']:
                raise ValueError('模板或 API 配置已变化，请重新配置计划')
            if value.get('bundle_id') and not PanelUploadBundle.objects.filter(pk=value['bundle_id'], owner=user).exists():
                raise ValueError('上传计划须由源文件所属账号创建和启用')
            validate_trigger({'trigger': task.trigger, 'trigger_args': task.trigger_args})
            value['authorized_by'] = creator.pk
        task.is_active = enabled
        task.command = encode(value)
        task.updated_by, task.updated_at = user, human_datetime()
        task.save(update_fields=['is_active', 'command', 'updated_by', 'updated_at'])
    return task


def actor(value, task):
    task = Task.objects.get(pk=task.pk)
    user = require(task.created_by_id, 'schedule.schedule.run', 'exec.panel.view', 'exec.panel.create', 'exec.panel.execute')
    if value.get('authorized_by') != user.pk:
        raise PermissionError('计划授权账号与真实创建者不一致')
    s.action_permission(user, value['operation'])
    resource_access.require_resources(user, 'panel', value['panel_ids'], manage=True)
    return user


def request_data(value):
    data = {'operation': value['operation'], 'panel_ids': value['panel_ids'],
            'params_by_panel': copy.deepcopy(value['params_by_panel'])}
    if value.get('bundle_id'):
        data['params'] = {'bundle_id': value['bundle_id']}
        data['params_by_panel'] = {pid: {'target_dir': p['target_dir']} for pid, p in value['params_by_panel'].items()}
    return data


def check_configuration(value):
    from apps.baota.models import Panel
    panels = list(Panel.objects.filter(pk__in=value['panel_ids']))
    if len(panels) != len(value['panel_ids']):
        raise ValueError('固定面板已被删除，请重新配置计划')
    for panel in panels:
        s.configured(panel, value['revisions'][str(panel.pk)])


def new_preflight(user, value):
    check_configuration(value)
    return s.prepare(user, request_data(value))


def dispatch(task_id):
    with transaction.atomic():
        task = Task.objects.select_for_update().filter(pk=task_id, interpreter=KIND).first()
        if not task or not task.is_active:
            return None
        try:
            if unresolved(task):
                return skipped(task, '上次运行尚未结束，本次触发已跳过')
            if task.latest and timezone.now() - last_activity(task.latest) < timedelta(seconds=MIN_INTERVAL):
                return skipped(task, '距离上次运行不足 300 秒，本次触发已跳过')
            value = definition(task)
        except (ValueError, KeyError, TypeError):
            return quarantine(task, '计划或历史数据无效，已停用；请核对后重新配置')
        output = {'kind': KIND, 'phase': 'preflight', 'revision': value['revision'], 'at': s.now(),
                  'panel_task_id': None, 'message': '正在重新预检'}
        history = History.objects.create(task_id=task.pk, status=0, run_time=human_datetime(), output=encode(output))
        try:
            user = actor(value, task)
            panel_task = new_preflight(user, value)
            s.audit(panel_task, user, 'schedule', schedule_id=task.pk, history_id=history.pk, revision=value['revision'])
            output['panel_task_id'] = panel_task.pk
        except (ValueError, PermissionError):
            history.status = 2
            output.update(phase='blocked', message='账号授权、API 配置或源文件已变化；计划已停用，请重新核对',
                          notification='pending')
            task.is_active = False
        history.output = encode(output)
        history.save(update_fields=['output', 'status'])
        task.latest = history
        task.save(update_fields=['latest', 'is_active'])
        return history


def last_activity(history):
    output = decode(history.output)
    instants = [datetime.fromisoformat(output['at'])]
    previous = PanelTask.objects.filter(pk=output.get('panel_task_id')).first()
    if previous:
        # Completion follows the actual write AND its readback. Starting the
        # cooldown here is conservative even when adapter guards were slow.
        instants.append(previous.updated_at)
        for target in previous.targets.all():
            instants.extend(datetime.fromisoformat(intent['at']) for child in target.children for intent in child['intents'])
            instants.extend(datetime.fromisoformat(intent['returned_at']) for child in target.children
                            for intent in child['intents'] if intent.get('returned_at'))
            if previous.operation == 'file_upload':
                instants.extend(datetime.fromisoformat(intent['at']) for intent in
                                target.upload_files.exclude(intent={}).values_list('intent', flat=True))
    return max(instants)


def skipped(task, message):
    # Deliberately do not move Task.latest: it pins the unresolved mutation and
    # is also the rate-limit clock, which skipped ticks must never postpone.
    return History.objects.create(task_id=task.pk, status=3, run_time=human_datetime(),
        output=encode({'kind': KIND, 'phase': 'skipped', 'message': message, 'panel_task_id': None, 'at': s.now()}))


def quarantine(task, message):
    task.is_active = False
    record = History.objects.create(task_id=task.pk, status=2, run_time=human_datetime(),
        output=encode({'kind': KIND, 'phase': 'blocked', 'message': message, 'panel_task_id': None,
                       'at': s.now(), 'notification': 'pending'}))
    # Preserve the durable link if an earlier mutation is still unresolved.
    if not task.latest or task.latest.status != 0:
        task.latest = record
    task.save(update_fields=['is_active', 'latest'])
    return record


def before_request(panel_task, mutating=False):
    """Revalidate durable plan/creator/targets for both reads and writes."""
    link = next((a for a in panel_task.audit if a.get('action') == 'schedule'), None)
    if not link:
        return
    task = Task.objects.filter(pk=link['schedule_id'], interpreter=KIND, is_active=True).first()
    if task is None:
        raise ValueError('面板计划已停止，禁止继续发送')
    value = definition(task)
    try:
        actor(value, task)
    except PermissionError:
        raise ValueError('面板计划授权已撤销，禁止继续发送') from None
    history = History.objects.filter(pk=link['history_id'], task_id=task.pk, status=0).first()
    envelope = decode(history.output) if history else {}
    if (value['revision'] != link['revision'] or history is None
            or envelope.get('panel_task_id') != panel_task.pk
            or envelope.get('phase') not in (('running',) if mutating else ('preflight', 'running'))
            or panel_task.owner_id != task.created_by_id
            or set(panel_task.targets.values_list('panel_id', flat=True)) != set(value['panel_ids'])):
        raise ValueError('面板计划运行授权已变化')
    if mutating and any(t.state in ('failed', 'uncertain', 'cancelled') or any(c['state'] in ('failed', 'uncertain', 'cancelled')
           for c in t.children) for t in panel_task.targets.all()):
        raise ValueError('本次面板计划已有异常，已停止后续发送')


def before_mutation(panel_task):
    return before_request(panel_task, mutating=True)


def lock_for_intent(panel_task_id):
    """Take the schedule lock BEFORE the panel task lock, like the coordinator.

    Disabling waits for any accepted intent transaction. An already accepted
    HTTP request cannot be recalled; final client checks also stop unsent calls.
    """
    audit = PanelTask.objects.values_list('audit', flat=True).get(pk=panel_task_id)
    link = next((a for a in audit if a.get('action') == 'schedule'), None)
    if link:
        Task.objects.select_for_update().filter(pk=link['schedule_id']).first()


class AuthorizedClient:
    def __init__(self, client, task):
        self.client, self.task = client, task

    def authorize(self):
        try:
            before_mutation(self.task)
        except ValueError:
            from apps.baota.client import PanelAPIError
            raise PanelAPIError('面板计划已停止或授权发生变化，未发送请求') from None

    def request(self, *args, **kwargs):
        if kwargs.get('mutating'):
            self.authorize()
        return self.client.request(*args, **kwargs)

    def upload_file(self, *args, **kwargs):
        self.authorize()
        return self.client.upload_file(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


def authorized_client(client, task):
    return AuthorizedClient(client, task) if any(a.get('action') == 'schedule' for a in task.audit) else client


def stop_run(task, history, panel_task, output, message):
    task.is_active = False
    task.save(update_fields=['is_active'])
    # Cancellation stops unsent work but keeps ambiguous accepted requests and
    # their panel locks available to the existing read-only reconciliation UI.
    if panel_task:
        panel_task.cancel_requested = True
        panel_task.save(update_fields=['cancel_requested', 'updated_at'])
        for target in panel_task.targets.select_for_update():
            if panel_task.operation == 'file_upload':
                from apps.baota import upload_runner as uploads
                uploads.cancel_unsent(target)
                if target.state in s.ACTIVE:
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
                    child['result'] = {'state': 'cancelled', 'message': message, 'evidence': {}}
            if target.state not in s.ACTIVE:
                if target.state in ('preflight_queued', 'prepared'):
                    target.state = 'cancelled'
                    target.save(update_fields=['state', 'children', 'updated_at'])
                else:
                    s.settle_target(target)
            else:
                target.save(update_fields=['children', 'updated_at'])
        s.refresh_task(panel_task)
    output.update(phase='blocked', message=message, notification='pending')
    history.status, history.output = 2, encode(output)
    history.save(update_fields=['status', 'output'])


def check_scope(value, panel_task):
    actual = {str(t.panel_id): [projection(c['snapshot']) for c in t.children] for t in panel_task.targets.all()}
    if actual != value['scope']:
        raise ValueError('目标范围或网站身份已变化，请重新配置计划')
    for target in panel_task.targets.all():
        if (target.state == 'failed' or not target.children or target.summary.get('upload', {}).get('failed')
                or any(c['state'] in ('failed', 'uncertain') for c in target.children)):
            raise ValueError('本次预检未全部通过，请查看逐台异常原因')
    if not set(s.required_acks(panel_task.targets.all())).issubset(value['acknowledgements']):
        raise ValueError('本次预检需要新的影响确认，请重新配置计划')


def tick(limit=20):
    ids = list(History.objects.filter(status=0, task_id__in=Task.objects.filter(interpreter=KIND))
               .order_by('id').values_list('id', flat=True)[:limit])
    for history_id in ids:
        with transaction.atomic():
            seed = History.objects.filter(pk=history_id).first()
            if seed is None:
                continue
            task = Task.objects.select_for_update().filter(pk=seed.task_id).first()
            history = History.objects.select_for_update().filter(pk=history_id).first()
            if history is None or history.status != 0:
                continue
            if task is None:
                history.status = 2
                history.output = encode({'kind': KIND, 'phase': 'blocked', 'message': '所属计划已删除', 'panel_task_id': None})
                history.save(update_fields=['status', 'output'])
                continue
            output = {'kind': KIND, 'phase': 'blocked', 'message': '', 'panel_task_id': None}
            panel_task = None
            try:
                output = decode(history.output)
                panel_task = PanelTask.objects.select_for_update().filter(pk=output.get('panel_task_id')).first()
                if panel_task is not None:
                    link = next((a for a in panel_task.audit if a.get('action') == 'schedule'), {})
                    if (link.get('schedule_id') != task.pk or link.get('history_id') != history.pk
                            or panel_task.owner_id != task.created_by_id):
                        # Never let corrupt historical IDs cancel or modify an
                        # unrelated durable task when stop_run handles failure.
                        panel_task = None
                        raise ValueError('面板运行记录不属于此计划')
                value = definition(task)
                if not task.is_active or value['revision'] != output['revision']:
                    raise ValueError('计划已停止或配置已变化，未发送项目已取消')
                actor(value, task)
                if panel_task is None:
                    raise ValueError('面板运行记录缺失，请核对后重新配置计划')
                targets = list(panel_task.targets.all())
                if panel_task.cancel_requested:
                    raise ValueError('面板任务已取消，计划已停用')
                if any(t.state in ('failed', 'uncertain') or any(c['state'] in ('failed', 'uncertain', 'cancelled')
                       for c in t.children) or t.summary.get('upload', {}).get('failed') for t in targets):
                    raise ValueError('面板任务执行异常或结果待核实，请查看逐项原因')
                if panel_task.state == 'preflight_queued':
                    if timezone.now() - panel_task.created_at > timedelta(hours=1):
                        raise ValueError('预检超过一小时仍未完成，计划已停止')
                elif output['phase'] == 'preflight':
                    check_configuration(value)
                    check_scope(value, panel_task)
                    if panel_task.state == 'prepared':
                        s.start(actor(value, task), panel_task.pk, {'acknowledgements': s.required_acks(targets)}, scheduled=True)
                        output.update(phase='running', message='预检通过，正在执行')
                    elif panel_task.state == 'completed':
                        output.update(phase='completed', message='预检通过，全部已符合目标')
                        history.status = 1
                    else:
                        raise ValueError('预检状态无效，计划已停止')
                elif panel_task.state == 'completed':
                    output.update(phase='completed', message='执行完成，结果已读回')
                    history.status = 1
                history.output = encode(output)
                history.save(update_fields=['status', 'output'])
                if history.status == 1 and task.trigger == 'date':
                    task.is_active = False
                    task.save(update_fields=['is_active'])
            except PermissionError:
                stop_run(task, history, panel_task, output, '执行账号已停用或权限被撤销，计划已停止')
            except ValueError as exc:
                stop_run(task, history, panel_task, output, str(exc))
            except (KeyError, TypeError, OverflowError):
                stop_run(task, history, panel_task, output, '面板计划或运行数据无效，请核对后重新配置')


def notify_pending(limit=5):
    """Separate scheduler job; claim once before the existing notification I/O.

    A timeout can mean accepted delivery, so we never automatically retry. The
    mutation coordinator remains entirely local and cannot be held up by a hook.
    """
    from .utils import send_fail_notify
    ids = list(History.objects.filter(status=2, output__contains='"notification": "pending"',
               task_id__in=Task.objects.filter(interpreter=KIND)).order_by('id').values_list('id', flat=True)[:limit])
    for history_id in ids:
        with transaction.atomic():
            history = History.objects.select_for_update().filter(pk=history_id).first()
            if history is None:
                continue
            output = decode(history.output)
            if output.get('kind') != KIND or output.get('notification') != 'pending':
                continue
            output['notification'] = 'attempted'
            history.output = encode(output)
            history.save(update_fields=['output'])
            task = Task.objects.filter(pk=history.task_id).first()
        if task:
            try:
                send_fail_notify(task, output['message'])
            except Exception:
                output['notification'] = 'failed'
                History.objects.filter(pk=history_id).update(output=encode(output))


def preview(user, task):
    task = access(user, task, 'run')
    require(user, 'exec.panel.create')
    return new_preflight(user, definition(task))


def history_detail(user, task, history):
    task = access(user, task)
    history = History.objects.get(pk=history.pk)
    if history.task_id != task.pk:
        raise PermissionError('历史记录不属于此计划')
    output = decode(history.output)
    panel_task = PanelTask.objects.filter(pk=output.get('panel_task_id')).first()
    rows = []
    if panel_task:
        s.task_access(user, panel_task)
        link = next((a for a in panel_task.audit if a.get('action') == 'schedule'), {})
        if link.get('schedule_id') != task.pk or link.get('history_id') != history.pk:
            raise PermissionError('面板任务不属于此计划运行')
        for target in panel_task.targets.all():
            reasons = [c.get('result', {}).get('message') or c['snapshot'].get('message', '') for c in target.children]
            state = target.state
            if any(c['state'] in ('failed', 'uncertain', 'cancelled') for c in target.children):
                state = 'failed' if any(c['state'] == 'failed' for c in target.children) else 'uncertain'
            elif target.state == 'completed' and target.children:
                outcomes = {c['state'] for c in target.children}
                if outcomes <= {'already', 'skipped'}:
                    state = 'already' if 'already' in outcomes else 'skipped'
                elif outcomes <= {'success', 'already', 'skipped'}:
                    state = 'success'
            rows.append({'name': target.panel_snapshot['name'], 'state': state,
                         'output': '\n'.join(dict.fromkeys([target.message, *reasons])).strip()})
    return {'kind': KIND, 'panel_task_id': panel_task.pk if panel_task else None,
            'phase': output['phase'], 'message': output['message'], 'status': history.status,
            'run_time': history.run_time, 'success': sum(r['state'] in ('success', 'already') for r in rows),
            'failure': sum(r['state'] in ('failed', 'uncertain', 'cancelled') for r in rows),
            'duration': None, 'outputs': rows}


def retained_bundle(bundle_id):
    # Called with the source bundle locked, matching save's bundle check. No
    # schedule row locks here (avoids reversing schedule -> bundle lock order).
    for command in Task.objects.filter(interpreter=KIND).values_list('command', flat=True).iterator():
        try:
            if decode(command).get('bundle_id') == bundle_id:
                return True
        except ValueError:
            # A corrupt panel definition is conservative: retain sources until
            # an administrator repairs/removes the definition.
            return True
    return False
