# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django_redis import get_redis_connection
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apps.schedule.scheduler import Scheduler
from apps.schedule.models import Task, History
from apps.schedule.executors import dispatch_job
from apps.baota.task_views import TaskBaseView
from . import panel_tasks
from . import resources
from apps.account import access
from apps.host.models import Host
from django.conf import settings
from libs import json_response, JsonParser, Argument, human_datetime, auth
import json


class Schedule(TaskBaseView):
    @auth('schedule.schedule.view')
    def get(self, request):
        tasks = []
        views = []
        for task in Task.objects.select_related('latest').all():
            try:
                if task.interpreter == 'baota':
                    panel_tasks.access(request.user, task)
                else:
                    resources.authorized(request.user, task)
            except (PermissionError, ValueError):
                continue
            tasks.append(task)
            item = task.to_dict()
            if task.latest_id:
                try:
                    if task.latest.task_id != task.pk:
                        raise PermissionError('历史记录不属于此计划')
                    if task.interpreter == 'baota':
                        panel_tasks.history_detail(request.user, task, task.latest)
                    else:
                        resources.history_access(request.user, task.latest)
                except (ValueError, PermissionError):
                    for field in ('latest_id', 'latest_status', 'latest_status_alias', 'latest_run_time'):
                        item[field] = None
            try:
                if task.interpreter == 'baota':
                    definition = panel_tasks.definition(task)
                    access.require_resources(request.user, 'panel', definition['panel_ids'], manage=True)
                    panel_tasks.s.action_permission(request.user, definition['operation'])
                else:
                    resources.definition_scope(request.user, task, manage=True)
                item['can_manage'] = True
            except (ValueError, PermissionError):
                item['can_manage'] = False
            views.append(item)
        types = sorted({x.type for x in tasks})
        return json_response({'types': types, 'tasks': views})

    @auth('schedule.schedule.add|schedule.schedule.edit')
    def post(self, request):
        data = self.body(request)
        access.require(request.user, 'schedule.schedule.' + ('edit' if data.get('id') else 'add'))
        if data.get('interpreter') == 'baota':
            panel_tasks.save(request.user, data)
            return json_response()
        if data.get('id') and Task.objects.filter(pk=data['id'], interpreter='baota').exists():
            return json_response(error='面板计划不能转换为脚本任务')
        if data.get('interpreter') not in ('sh', 'python'):
            return json_response(error='请选择有效的任务内容类型')
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('type', help='请输入任务类型'),
            Argument('name', help='请输入任务名称'),
            Argument('interpreter', help='请选择执行解释器'),
            Argument('command', help='请输入任务内容'),
            Argument('rst_notify', type=dict, help='请选择执行失败通知方式'),
            Argument('targets', type=list, filter=lambda x: len(x), help='请选择执行对象'),
            Argument('trigger', filter=lambda x: x in dict(Task.TRIGGERS), help='请选择触发器类型'),
            Argument('trigger_args', help='请输入触发器参数'),
            Argument('desc', required=False),
        ).parse(request.body)
        if error is None:
            resources.targets(request.user, form.targets, manage=True, monitor=form.trigger == 'monitor')
            creator_id = request.user.pk
            if form.id:
                creator_id = resources.authorized(request.user, form.id, 'edit').created_by_id
            proposed = Task(created_by_id=creator_id, targets=json.dumps(form.targets),
                            trigger=form.trigger, trigger_args=form.trigger_args)
            resources.definition_scope(request.user, proposed, manage=True)
            resources.definition_scope(creator_id, proposed, manage=True)
            form.targets = json.dumps(form.targets)
            form.rst_notify = json.dumps(form.rst_notify)
            if form.trigger == 'cron':
                args = json.loads(form.trigger_args)['rule'].split()
                if len(args) != 5:
                    return json_response(error='无效的执行规则，请更正后再试')
                minute, hour, day, month, week = args
                week = '0' if week == '7' else week
                try:
                    CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=week)
                except ValueError:
                    return json_response(error='无效的执行规则，请更正后再试')
            if form.id:
                Task.objects.filter(pk=form.id).update(
                    updated_at=human_datetime(),
                    updated_by=request.user,
                    **form
                )
                task = Task.objects.filter(pk=form.id).first()
                if task and task.is_active:
                    form.action = 'modify'
                    form.targets = json.loads(form.targets)
                    rds_cli = get_redis_connection()
                    rds_cli.lpush(settings.SCHEDULE_KEY, json.dumps(form))
            else:
                Task.objects.create(created_by=request.user, **form)
        return json_response(error=error)

    @auth('schedule.schedule.enable')
    def patch(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定操作对象'),
            Argument('is_active', type=bool, required=False)
        ).parse(request.body, True)
        if error is None:
            task = Task.objects.get(pk=form.id)
            if task.interpreter == 'baota':
                data = self.body(request)
                panel_tasks.activate(request.user, task.pk, form.get('is_active'), data.get('confirm_panel', False))
                # The scheduler reconciles these durable DB definitions every
                # five seconds; Redis delivery cannot lose an activation.
                return json_response()
            resources.authorized(request.user, task, 'enable')
            if form.get('is_active') is True:
                resources.authorized(task.created_by_id, task, 'run')
            if form.get('is_active') is not None:
                task.is_active = form.is_active
                task.latest_id = None
                if form.is_active:
                    message = {'id': form.id, 'action': 'add'}
                    message.update(task.to_dict(selects=('interpreter', 'trigger', 'trigger_args', 'command', 'targets')))
                else:
                    message = {'id': form.id, 'action': 'remove'}
                rds_cli = get_redis_connection()
                rds_cli.lpush(settings.SCHEDULE_KEY, json.dumps(message))
            task.save()
        return json_response(error=error)

    @auth('schedule.schedule.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定操作对象')
        ).parse(request.GET)
        if error is None:
            task = Task.objects.filter(pk=form.id).first()
            if task:
                if task.interpreter == 'baota':
                    panel_tasks.access(request.user, task, 'del')
                    if panel_tasks.unresolved(task):
                        return json_response(error='运行尚未结束，请先核验面板任务结果')
                else:
                    resources.authorized(request.user, task, 'del')
                if task.is_active:
                    return json_response(error='该任务在运行中，请先停止任务再尝试删除')
                task.delete()
                History.objects.filter(task_id=task.id).delete()
        return json_response(error=error)


class HistoryView(TaskBaseView):
    @auth('schedule.schedule.view')
    def get(self, request, t_id):
        task = Task.objects.filter(pk=t_id).first()
        if not task:
            return json_response(error='未找到指定任务')
        if task.interpreter == 'baota':
            panel_tasks.access(request.user, task)
        else:
            resources.authorized(request.user, task)

        h_id = request.GET.get('id')
        if h_id:
            h_id = task.latest_id if h_id == 'latest' else h_id
            record = History.objects.filter(pk=h_id, task_id=task.pk).first()
            if record is None:
                return json_response(error='未找到此计划的执行记录')
            if task.interpreter == 'baota':
                return json_response(panel_tasks.history_detail(request.user, task, record))
            return json_response(self._fetch_detail(record, request.user))
        histories = History.objects.filter(task_id=t_id)
        visible = []
        for record in histories:
            try:
                if task.interpreter == 'baota':
                    panel_tasks.history_detail(request.user, task, record)
                else:
                    resources.history_access(request.user, record)
            except (ValueError, PermissionError):
                continue
            visible.append(record.to_list())
        return json_response(visible)

    @auth('schedule.schedule.run')
    def post(self, request, t_id):
        task = Task.objects.filter(pk=t_id).first()
        if not task:
            return json_response(error='未找到指定任务')
        if task.interpreter == 'baota':
            preview = panel_tasks.preview(request.user, task)
            return json_response({'panel_task_id': preview.pk, 'mode': 'preflight'})
        task = resources.authorized(request.user, task, 'run')
        selected = json.loads(task.targets)
        resources.targets(request.user, selected, manage=True)
        authorize = lambda: resources.runtime(task.pk, task.interpreter, task.command, selected, active=False)
        authorize()
        history = History.objects.create(task_id=task.pk, status=0, run_time=human_datetime(),
                                         output=json.dumps({str(host_id): None for host_id in selected}))
        for host_id in selected:
            authorize = lambda selected_host=host_id: resources.runtime_for_history(history.pk, task.pk, selected_host,
                task.interpreter, task.command, active=False)
            try:
                code, duration, out = dispatch_job(host_id, task.interpreter, task.command, authorize=authorize)
                if not resources.save_history_result(history.pk, task.pk, host_id, [code, duration, out]):
                    break
            except Exception:
                resources.fail_history(history.pk, '计划授权、配置或调用结果已变化，未继续执行；已有调用需核验')
                break
        return json_response(history.id)

    def _fetch_detail(self, record, user):
        record = resources.history_access(user, record)
        outputs = json.loads(record.output)
        host_ids = (x for x in outputs.keys() if x != 'local')
        hosts_info = {str(x.id): x.name for x in Host.objects.filter(id__in=host_ids)}
        data = {'run_time': record.run_time, 'success': 0, 'failure': 0, 'duration': 0, 'outputs': []}
        for host_id, value in outputs.items():
            if not value:
                continue
            code, duration, out = value
            key = 'success' if code == 0 else 'failure'
            data[key] += 1
            data['duration'] += duration
            data['outputs'].append({
                'name': hosts_info.get(host_id, '本机'),
                'code': code,
                'duration': duration,
                'output': out})
        data['duration'] = f"{data['duration'] / max(len(outputs), 1):.3f}"
        return data


class PanelTemplatesView(TaskBaseView):
    def get(self, request):
        if request.GET.get('id'):
            return json_response(panel_tasks.template(request.user, request.GET['id']))
        return json_response(panel_tasks.templates(request.user))


@auth('schedule.schedule.view|schedule.schedule.add|schedule.schedule.edit')
def next_run_time(request):
    form, error = JsonParser(
        Argument('rule', help='参数错误'),
        Argument('start', required=False),
        Argument('stop', required=False)
    ).parse(request.body)
    if error is None:
        try:
            minute, hour, day, month, week = form.rule.split()
            week = Scheduler.covert_week(week)
            trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=week,
                                  start_date=form.start, end_date=form.stop)
        except (ValueError, KeyError):
            return json_response({'success': False, 'msg': '无效的执行规则'})
        scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
        scheduler.start()
        job = scheduler.add_job(lambda: None, trigger)
        run_time = job.next_run_time
        scheduler.shutdown()
        if run_time:
            return json_response({'success': True, 'msg': run_time.strftime('%Y-%m-%d %H:%M:%S')})
        else:
            return json_response({'success': False, 'msg': '无法被触发'})
    return json_response(error=error)
