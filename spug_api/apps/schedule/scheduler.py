# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from django_redis import get_redis_connection
from django.db import connections
from django.db.utils import DatabaseError
from apps.schedule.models import Task, History
from apps.schedule.builtin import auto_run_by_day, auto_run_by_minute
from django.conf import settings
from libs import AttrDict, human_datetime
import logging
import json

SCHEDULE_WORKER_KEY = settings.SCHEDULE_WORKER_KEY


class Scheduler:
    timezone = settings.TIME_ZONE
    week_map = {
        '/': '/',
        '-': '-',
        ',': ',',
        '*': '*',
        '7': '6',
        '0': '6',
        '1': '0',
        '2': '1',
        '3': '2',
        '4': '3',
        '5': '4',
        '6': '5',
    }

    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=self.timezone, executors={'default': ThreadPoolExecutor(30)})
        self.panel_specs = {}

    def _sync_panel_jobs(self):
        """DB reconciliation is the durable activation outbox for panel plans."""
        active = set()
        try:
            for task in Task.objects.filter(interpreter='baota', is_active=True):
                job_id = 'panel-schedule-' + str(task.pk)
                active.add(job_id)
                signature = (task.trigger, task.trigger_args, task.command)
                if self.panel_specs.get(job_id) == signature:
                    continue
                trigger = self.parse_trigger(task.trigger, task.trigger_args)
                self.scheduler.add_job(self.dispatch, trigger, id=job_id,
                                       args=(task.pk, 'baota', '', []), replace_existing=True,
                                       max_instances=1, coalesce=True, misfire_grace_time=1)
                self.panel_specs[job_id] = signature
            for job_id in set(self.panel_specs) - active:
                job = self.scheduler.get_job(job_id)
                if job:
                    job.remove()
                del self.panel_specs[job_id]
        finally:
            connections.close_all()

    @classmethod
    def covert_week(cls, week_str):
        return ''.join(map(lambda x: cls.week_map[x], week_str))

    @classmethod
    def parse_trigger(cls, trigger, trigger_args):
        if trigger == 'interval':
            return IntervalTrigger(seconds=int(trigger_args), timezone=cls.timezone)
        elif trigger == 'date':
            return DateTrigger(run_date=trigger_args, timezone=cls.timezone)
        elif trigger == 'cron':
            args = json.loads(trigger_args) if not isinstance(trigger_args, dict) else trigger_args
            minute, hour, day, month, week = args['rule'].split()
            week = cls.covert_week(week)
            return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=week,
                               start_date=args['start'], end_date=args['stop'])
        else:
            raise TypeError(f'unknown schedule policy: {trigger!r}')

    @classmethod
    def dispatch(cls, task_id, interpreter, command, targets):
        # Redis/job arguments may predate an edit or a disable. Re-read the
        # current definition; a panel payload must never enter the SSH queue.
        task = Task.objects.filter(pk=task_id, is_active=True).first()
        if not task:
            connections.close_all()
            return
        if task.interpreter == 'baota':
            from .panel_tasks import dispatch
            try:
                return dispatch(task_id)
            finally:
                connections.close_all()
        saved_targets = json.loads(task.targets)
        # Monitor events resolve this placeholder to the host that raised the
        # alarm. Retain that trusted caller resolution for legacy scripts.
        if task.trigger != 'monitor' or 'monitor' not in saved_targets:
            targets = saved_targets
        interpreter, command = task.interpreter, task.command
        if interpreter not in ('sh', 'python'):
            connections.close_all()
            return
        output = {x: None for x in targets}
        history = History.objects.create(
            task_id=task_id,
            status='0',
            run_time=human_datetime(),
            output=json.dumps(output)
        )
        Task.objects.filter(pk=task_id).update(latest_id=history.id)
        rds_cli = get_redis_connection()
        for t in targets:
            rds_cli.rpush(SCHEDULE_WORKER_KEY, json.dumps([history.id, t, interpreter, command]))
        connections.close_all()

    def _init_builtin_jobs(self):
        from apps.ops.expiry.reminders import scheduled_check_expiry
        from apps.monitor.ssl.jobs import scheduled_tick as ssl_monitor_tick
        from .panel_tasks import notify_pending
        self.scheduler.add_job(auto_run_by_day, 'cron', hour=1, minute=20)
        self.scheduler.add_job(auto_run_by_minute, 'interval', minutes=1)
        self.scheduler.add_job(ssl_monitor_tick, 'interval', seconds=10,
                               id='ssl-monitor-worker', max_instances=1, coalesce=True,
                               misfire_grace_time=1, replace_existing=True)
        self.scheduler.add_job(self._sync_panel_jobs, 'interval', seconds=5,
                               id='panel-schedule-sync', max_instances=1, coalesce=True)
        self.scheduler.add_job(notify_pending, 'interval', seconds=5,
                               id='panel-schedule-notifications', max_instances=1, coalesce=True)
        self.scheduler.add_job(scheduled_check_expiry, 'cron', id='server-expiry-daily',
                               hour=9, minute=0, timezone=self.timezone,
                               max_instances=1, coalesce=True, misfire_grace_time=86400,
                               replace_existing=True)

    def _init(self):
        self.scheduler.start()
        self._init_builtin_jobs()
        try:
            for task in Task.objects.filter(is_active=True).exclude(trigger='monitor').exclude(interpreter='baota'):
                trigger = self.parse_trigger(task.trigger, task.trigger_args)
                self.scheduler.add_job(
                    self.dispatch,
                    trigger,
                    id=str(task.id),
                    args=(task.id, task.interpreter, task.command, json.loads(task.targets)),
                )
            connections.close_all()
            self._sync_panel_jobs()
        except DatabaseError:
            pass

    def run(self):
        rds_cli = get_redis_connection()
        self._init()
        rds_cli.delete(settings.SCHEDULE_KEY)
        logging.warning('Running scheduler')
        while True:
            _, data = rds_cli.brpop(settings.SCHEDULE_KEY)
            task = AttrDict(json.loads(data))
            if Task.objects.filter(pk=task.id, interpreter='baota').exists():
                self._sync_panel_jobs()
                continue
            if task.action in ('add', 'modify'):
                trigger = self.parse_trigger(task.trigger, task.trigger_args)
                self.scheduler.add_job(
                    self.dispatch,
                    trigger,
                    id=str(task.id),
                    args=(task.id, task.interpreter, task.command, task.targets),
                    replace_existing=True
                )
            elif task.action == 'remove':
                job = self.scheduler.get_job(str(task.id))
                if job:
                    job.remove()
