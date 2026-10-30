"""One frozen target per step, with durable claims and no automatic retries."""
import hmac
import threading

from django.db import close_old_connections, transaction
from django.utils import timezone

from . import catalogue, service
from .models import OpsRun, OpsTarget
from .runner import MESSAGES, ProbeError, run_probe


@transaction.atomic
def recover_interrupted():
    """Only call while holding the dedicated process lock, before dispatch."""
    for run in OpsRun.objects.select_for_update().filter(state__in=('queued', 'running', 'cancelled')).order_by('id'):
        interrupted = run.targets.filter(state='running').update(
            state='unknown', category='interrupted', message=MESSAGES['interrupted'], finished_at=timezone.now())
        if not interrupted:
            continue
        run.batch_done += interrupted
        run.batch_failures += interrupted
        run.state = 'cancelled' if run.cancel_requested else 'tripped'
        run.save(update_fields=['state', 'batch_done', 'batch_failures', 'updated_at'])
        service.audit(run, 'interrupted', detail={'targets': interrupted})


class OpsWorker:
    def __init__(self, probe=run_probe):
        self.probe = probe
        self.stop_event = threading.Event()

    @transaction.atomic
    def claim_next(self):
        if self.stop_event.is_set():
            return None
        # Lock order is always run -> target. Another coordinator sees the
        # in-flight target after acquiring the run lock and cannot double claim.
        run = (OpsRun.objects.select_for_update().filter(state__in=('queued', 'running'), cancel_requested=False)
               .order_by('id').first())
        if run is None or run.state not in ('queued', 'running') or run.cancel_requested:
            return None
        if run.targets.filter(state='running').exists():
            return None
        if run.batch_done >= run.batch_size:
            run.state = 'paused'
            run.save(update_fields=['state', 'updated_at'])
            return None
        target = run.targets.select_for_update().filter(state='pending').first()
        if target is None:
            run.state = 'completed'
            run.save(update_fields=['state', 'updated_at'])
            return None
        # The state CAS also protects backends where SELECT FOR UPDATE is a no-op.
        changed = OpsTarget.objects.filter(pk=target.pk, state='pending').update(
            state='running', started_at=timezone.now())
        if not changed:
            return None
        run.state = 'running'
        run.save(update_fields=['state', 'updated_at'])
        service.audit(run, 'dispatch_claimed', detail={'target_id': target.id})
        return target.id

    @transaction.atomic
    def finish(self, target_id, checks, category='', uncertain=False):
        run_id = OpsTarget.objects.values_list('run_id', flat=True).get(pk=target_id)
        run = OpsRun.objects.select_for_update().get(pk=run_id)
        target = OpsTarget.objects.select_for_update().get(pk=target_id)
        if target.state != 'running':
            return
        target.state = 'unknown' if uncertain else ('failed' if category else 'success')
        target.category = category
        target.message = MESSAGES.get(category, '只读检查完成；告警与未采集字段请查看检查项')
        if uncertain:
            target.message = '检查发送或采集结果待核实，不会自动重发；请检查主机状态'
        target.checks = checks if not category else []
        target.finished_at = timezone.now()
        target.save(update_fields=['state', 'category', 'message', 'checks', 'finished_at'])
        run.batch_done += 1
        run.batch_failures += int(bool(category))
        if run.cancel_requested:
            run.state = 'cancelled'
        elif run.batch_failures >= run.failure_threshold:
            run.state = 'tripped'
        elif not run.targets.filter(state='pending').exists():
            run.state = 'completed'
        elif run.batch_done >= run.batch_size:
            run.state = 'paused'
        else:
            run.state = 'running'
        run.save(update_fields=['state', 'batch_done', 'batch_failures', 'updated_at'])
        service.audit(run, 'target_finished', detail={'target_id': target.id, 'state': target.state,
                                                     'category': category})

    def run_once(self):
        target_id = self.claim_next()
        if target_id is None:
            return False
        target = OpsTarget.objects.select_related('run__created_by', 'host').get(pk=target_id)
        try:
            service.require_admin(target.run.created_by)
        except ValueError:
            self.finish(target_id, [], 'permission_denied')
            return True
        if target.host is None or not hmac.compare_digest(target.identity_digest, service.identity(target.host)):
            self.finish(target_id, [], 'host_changed')
            return True
        try:
            current = catalogue.get_job(target.run.job)
            if current['version'] != target.run.version:
                raise ProbeError('check_error')
            # A claim is the dispatch boundary: cancellation may drain this
            # bounded read-only call, but no pending target will be claimed.
            checks = self.probe(target.host, target.run.job, target.run.params)
            self.finish(target_id, checks)
        except ProbeError as exc:
            self.finish(target_id, [], exc.category, uncertain=exc.uncertain)
        except Exception:
            self.finish(target_id, [], 'check_error')
        return True

    def run(self):
        while not self.stop_event.is_set():
            close_old_connections()
            if not self.run_once():
                self.stop_event.wait(2)
