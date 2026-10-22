"""One durable child/phase per step; no network call runs inside a transaction."""
import copy
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .client import PanelClient
from .models import Panel, PanelTask, PanelTaskTarget, PanelLock
from .operations import get_operation
from . import task_service as s


POLL_INTERVAL_SECONDS = 5
MAX_POLL_SECONDS = 900
MAX_POLLS = 180


class WorkerStopping(Exception):
    pass


class StopAwareClient:
    """Drain the current bounded HTTP call, then prevent the next network call."""
    def __init__(self, client, stop_event):
        self.client, self.stop_event = client, stop_event

    def request(self, *args, **kwargs):
        if self.stop_event.is_set():
            raise WorkerStopping()
        return self.client.request(*args, **kwargs)

    def read_rewrite_template(self, *args, **kwargs):
        if self.stop_event.is_set():
            raise WorkerStopping()
        return self.client.read_rewrite_template(*args, **kwargs)

    def read_issued_certificate(self, *args, **kwargs):
        if self.stop_event.is_set():
            raise WorkerStopping()
        return self.client.read_issued_certificate(*args, **kwargs)

    def read_site_web_config(self, *args, **kwargs):
        if self.stop_event.is_set():
            raise WorkerStopping()
        return self.client.read_site_web_config(*args, **kwargs)

    def read_site_public_certificate(self, *args, **kwargs):
        if self.stop_event.is_set():
            raise WorkerStopping()
        return self.client.read_site_public_certificate(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


def locked_target(target_id):
    task_id = PanelTaskTarget.objects.values_list('task_id', flat=True).get(pk=target_id)
    task = PanelTask.objects.select_for_update().get(pk=task_id)
    target = task.targets.select_for_update().select_related('panel').get(pk=target_id)
    return task, target


def uncertain(child, message='调用结果不确定，仅允许只读核验或人工处理'):
    child['state'] = 'uncertain'
    child['uncertain_provenance'] = True
    remote = {**child.get('result', {}).get('remote', {}), 'ambiguous': True}
    remote.pop('next_action', None)
    child['result'] = {'state': 'uncertain', 'message': message, 'evidence': {}, 'remote': remote}


def recover_interrupted():
    """Run only after obtaining the process file lock, before starting any threads."""
    ids = list(PanelTaskTarget.objects.filter(
        state__in=('preflighting', 'running', 'dispatching', 'reconciling', 'remote')).values_list('pk', flat=True))
    for target_id in ids:
        with transaction.atomic():
            task, target = locked_target(target_id)
            if target.state == 'preflighting':
                target.state = 'cancelled' if task.cancel_requested else 'preflight_queued'
                target.save(update_fields=['state', 'updated_at'])
            else:
                for child in target.children:
                    if child['state'] in ('dispatching', 'remote', 'reconciling'):
                        uncertain(child, 'Worker 中断，发送结果或后续阶段未确认；不会自动重发')
                s.settle_target(target)
            s.refresh_task(task)


class PanelWorker:
    def __init__(self, client_factory=PanelClient, max_parallel=2):
        if type(max_parallel) is not int or not 1 <= max_parallel <= 2:
            raise ValueError('面板并行数必须为 1 或 2')
        self.client_factory = client_factory
        self.max_parallel = max_parallel
        self.stop_event = threading.Event()
        self.inflight = set()

    def claim_next(self):
        if self.stop_event.is_set():
            return None
        candidates = PanelTaskTarget.objects.filter(
            Q(state__in=('preflight_queued', 'queued', 'remote'))
            | Q(state='uncertain', reconcile_requested=True)).order_by('id')
        # Materialize IDs before opening write transactions; no streaming cursor
        # is kept across commits, and old pending panels cannot starve new work.
        for target_id in list(candidates.values_list('id', flat=True)):
            if self.stop_event.is_set():
                return None
            if target_id in self.inflight:
                continue
            with transaction.atomic():
                task, target = locked_target(target_id)
                if target.state == 'preflight_queued':
                    if task.cancel_requested:
                        target.state = 'cancelled'
                        target.save(update_fields=['state', 'updated_at'])
                        s.refresh_task(task)
                        continue
                    target.state = 'preflighting'
                elif target.state in ('queued', 'remote', 'uncertain'):
                    if target.state == 'uncertain' and not target.reconcile_requested:
                        continue
                    if target.state == 'remote' and not target.reconcile_requested:
                        pending = next((c for c in target.children if c['state'] == 'remote'), None)
                        if not pending or pending.get('next_poll_at', '') > s.now():
                            continue
                    # All mutation claims lock the stable panel row, then the unique
                    # panel lock. This also serializes get/create on MariaDB.
                    Panel.objects.select_for_update().get(pk=target.panel_id)
                    lock = PanelLock.objects.filter(panel_id=target.panel_id).first()
                    if lock and lock.target_id != target.pk:
                        continue
                    if lock is None:
                        PanelLock.objects.create(panel_id=target.panel_id, target=target)
                    target.state = 'running'
                else:
                    continue
                target.save(update_fields=['state', 'updated_at'])
                s.refresh_task(task)
                return target
        return None

    def run_once(self):
        target = self.claim_next()
        if target is None:
            return False
        self.process_target(target.pk)
        return True

    @contextmanager
    def client(self, panel):
        with self.client_factory(panel) as client:
            yield StopAwareClient(client, self.stop_event)

    def process_target(self, target_id):
        target = PanelTaskTarget.objects.select_related('task', 'panel').get(pk=target_id)
        if target.state == 'preflighting':
            self.preflight(target)
            return
        if target.state != 'running':
            return
        # A remote operation blocks later children on this same panel.
        index = next((i for i, c in enumerate(target.children) if c['state'] in ('remote', 'uncertain')), None)
        if index is not None:
            child = target.children[index]
            remote = child.get('result', {}).get('remote', {})
            if (child['state'] == 'remote' and remote.get('next_action') == 'deploy'
                    and not remote.get('ambiguous') and not child.get('uncertain_provenance')
                    and not target.task.cancel_requested):
                self.dispatch(target_id, index, 'deploy')
            else:
                self.readback(target_id, index)
            return
        index = next((i for i, c in enumerate(target.children) if c['state'] == 'ready'), None)
        if index is not None:
            self.dispatch(target_id, index, 'apply')
        else:
            with transaction.atomic():
                task, current = locked_target(target_id)
                s.settle_target(current)
                s.refresh_task(task)

    def preflight(self, target):
        try:
            s.configured(target.panel, target.panel_snapshot['api_config_revision'])
            with self.client(target.panel) as client:
                preview = get_operation(target.task.operation).preflight(client, copy.deepcopy(target.params))
            s.bounded(preview)
            items = preview['items']
            if not isinstance(items, list) or len(items) > s.MAX_CHILDREN:
                raise ValueError('预检子项超出限制')
            keys = set()
            children = []
            for item in items:
                if (not isinstance(item, dict) or set(item) - {'key', 'label', 'target', 'before', 'plan', 'state', 'message'}
                        or not isinstance(item.get('key'), str) or item['key'] in keys
                        or item.get('state') not in ('ready', 'already', 'skipped', 'failed')):
                    raise ValueError('预检结果格式无效')
                keys.add(item['key'])
                children.append({'snapshot': item, 'state': item['state'], 'intents': [],
                                 'result': {} if item['state'] == 'ready' else {
                                     'state': item['state'], 'message': item.get('message', ''), 'evidence': {}},
                                 'resolutions': []})
            message = '预检完成；尚未授权执行'
            state = 'prepared'
        except WorkerStopping:
            children, state, message = [], 'preflight_queued', 'Worker 正在停止，等待重试只读预检'
        except Exception:
            children, state, message = [], 'failed', '预检失败，请核对面板配置、资源或参数后重新创建任务'
        with transaction.atomic():
            task, current = locked_target(target.pk)
            if current.state != 'preflighting':
                return
            # The task row lock serializes concurrent panel preflight completions.
            existing = list(task.targets.exclude(pk=target.pk))
            existing_count = sum(len(value.children) for value in existing)
            if existing_count + len(children) > s.MAX_CHILDREN:
                children, state, message = [], 'failed', '整个任务最多 200 个子项，请缩小范围'
            current.children, current.summary = children, s.preflight_summary(children)
            if task.cancel_requested:
                for child in children:
                    if child['state'] == 'ready':
                        child['state'] = 'cancelled'
            try:
                s.check_preflight_size([*existing, current])
            except ValueError as exc:
                children, state, message = [], 'failed', str(exc)
                current.children, current.summary = children, s.preflight_summary(children)
            current.message = message
            if task.cancel_requested:
                state = 'cancelled'
            current.state = state
            current.save(update_fields=['children', 'summary', 'message', 'state', 'updated_at'])
            s.refresh_task(task)

    def begin_dispatch(self, target_id, index, phase):
        with transaction.atomic():
            task, target = locked_target(target_id)
            if target.state != 'running' or phase not in ('apply', 'deploy'):
                return None
            if self.stop_event.is_set():
                s.settle_target(target)
                s.refresh_task(task)
                return None
            child = target.children[index]
            if task.cancel_requested:
                if not child['intents']:
                    child['state'] = 'cancelled'
                else:
                    uncertain(child, '已取消后续发送阶段，已有远端操作需核验')
                s.settle_target(target)
                s.refresh_task(task)
                return None
            try:
                s.configured(target.panel, target.panel_snapshot['api_config_revision'])
                required = child['snapshot'].get('plan', {}).get('required_acknowledgements', [])
                if (any(code not in s.ACK_CODES for code in required)
                        or not set(required).issubset(task.acknowledgements)):
                    raise ValueError('缺少冻结确认项')
                if any(intent['phase'] == phase for intent in child['intents']):
                    raise ValueError('禁止重复发送同一阶段')
                if phase == 'apply' and child['state'] != 'ready':
                    raise ValueError('子项尚未准备好')
                remote = child.get('result', {}).get('remote', {})
                if phase == 'deploy' and (child['state'] != 'remote' or remote.get('next_action') != 'deploy'
                                          or remote.get('ambiguous') or child.get('uncertain_provenance')):
                    raise ValueError('不允许发送后续部署阶段')
            except ValueError:
                if child['intents']:
                    uncertain(child, '配置、确认项或阶段条件变化，需要人工核验')
                else:
                    child['state'] = 'failed'
                    child['result'] = {'state': 'failed', 'message': '预检后 API 配置或确认条件变化，请重新预检', 'evidence': {}}
                s.settle_target(target)
                s.refresh_task(task)
                return None
            child['intents'].append({'phase': phase, 'at': s.now(), 'status': 'dispatching',
                                     'api_config_revision': target.panel.api_config_revision})
            child['state'] = 'dispatching'
            target.state = 'dispatching'
            target.save(update_fields=['children', 'state', 'updated_at'])
            s.refresh_task(task)
            return target

    def dispatch(self, target_id, index, phase):
        target = self.begin_dispatch(target_id, index, phase)
        if target is None:
            return
        child = target.children[index]
        try:
            with self.client(target.panel) as client:
                result = get_operation(target.task.operation).dispatch(
                    client, copy.deepcopy(child['snapshot']), phase,
                    copy.deepcopy(child.get('result', {}).get('remote', {})))
            result = self.result(result)
        except Exception:
            result = {'state': 'uncertain', 'message': '调用中断或结果无法安全确认；不会自动重发',
                      'evidence': {}, 'remote': {'ambiguous': True, 'phase': phase}}
        self.save_result(target_id, index, result, dispatched=True)

    @staticmethod
    def result(value):
        if (not isinstance(value, dict) or value.get('state') not in s.TERMINAL | {'remote', 'uncertain'}
                or not isinstance(value.get('message'), str) or len(value['message']) > 1000):
            raise ValueError('适配器返回格式无效')
        result = {k: value[k] for k in ('state', 'message', 'evidence', 'remote') if k in value}
        return s.bounded(s.safe_tree(result))

    def readback(self, target_id, index):
        with transaction.atomic():
            task, target = locked_target(target_id)
            if target.state != 'running':
                return
            child = target.children[index]
            target.state = 'reconciling'
            target.reconcile_requested = False
            target.save(update_fields=['state', 'reconcile_requested', 'updated_at'])
        try:
            # Never use edited API credentials/base URL to infer the old request's outcome.
            s.configured(target.panel, target.panel_snapshot['api_config_revision'])
            with self.client(target.panel) as client:
                result = get_operation(task.operation).reconcile(
                    client, copy.deepcopy(child['snapshot']), copy.deepcopy(child.get('result', {}).get('remote', {})))
            result = self.result(result)
        except Exception:
            result = {'state': 'uncertain', 'message': '只读核验暂时无法确认结果，请人工核对',
                      'evidence': {}, 'remote': child.get('result', {}).get('remote', {})}
        self.save_result(target_id, index, result)

    def save_result(self, target_id, index, result, dispatched=False):
        with transaction.atomic():
            task, target = locked_target(target_id)
            child = target.children[index]
            previous_remote = child.get('result', {}).get('remote', {})
            if dispatched:
                child['intents'][-1]['status'] = 'returned'
                child['intents'][-1]['returned_at'] = s.now()
                child['intents'][-1]['outcome'] = result['state']
            sticky = child.get('uncertain_provenance') or previous_remote.get('ambiguous') or result.get('remote', {}).get('ambiguous')
            if result['state'] == 'uncertain' or sticky:
                child['uncertain_provenance'] = True
                result = {**result, 'state': 'uncertain',
                          'remote': {**previous_remote, **result.get('remote', {}), 'ambiguous': True}}
                result['remote'].pop('next_action', None)
            child['state'], child['result'] = result['state'], result
            if child['state'] == 'remote':
                child.setdefault('poll_started_at', s.now())
                child['polls'] = child.get('polls', 0) + (0 if dispatched else 1)
                elapsed = (timezone.now() - timezone.datetime.fromisoformat(child['poll_started_at'])).total_seconds()
                if elapsed >= MAX_POLL_SECONDS or child['polls'] >= MAX_POLLS:
                    uncertain(child, '远端处理超出自动核验时限，请人工核对；不会自动重发')
                child['next_poll_at'] = (timezone.now() + timedelta(seconds=POLL_INTERVAL_SECONDS)).isoformat()
                if result.get('remote', {}).get('next_action') == 'deploy':
                    child['next_poll_at'] = s.now()
            # Cancellation can arrive while the call is in flight. Only new work is cancelled.
            if task.cancel_requested:
                for sibling in target.children:
                    if sibling['state'] == 'ready' and not sibling['intents']:
                        sibling['state'] = 'cancelled'
            s.bounded(target.children, maximum=4 * s.MAX_JSON_BYTES)
            s.settle_target(target)
            s.refresh_task(task)

    def threaded_target(self, target_id):
        close_old_connections()
        try:
            self.process_target(target_id)
        finally:
            connection.close()

    def run(self):
        # SQLite's in-memory connection cannot be shared safely by this queue's
        # coordinator and worker threads; production uses MariaDB.
        if connection.vendor == 'sqlite':
            while not self.stop_event.is_set():
                if not self.run_once():
                    self.stop_event.wait(0.5)
            return
        futures = {}
        failed = False
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            while not self.stop_event.is_set() or futures:
                for future, target_id in list(futures.items()):
                    if future.done():
                        self.inflight.discard(target_id)
                        del futures[future]
                        # Do not log exceptions, which might contain transport data.
                        if future.exception() is not None:
                            failed = True
                            self.stop_event.set()
                if not self.stop_event.is_set() and len(futures) < self.max_parallel:
                    target = self.claim_next()
                    if target:
                        self.inflight.add(target.pk)
                        futures[pool.submit(self.threaded_target, target.pk)] = target.pk
                        continue
                self.stop_event.wait(0.25) if not self.stop_event.is_set() else time.sleep(0.05)
        if failed:
            raise RuntimeError('面板 worker 调用中断，请重启并核对保留状态')
