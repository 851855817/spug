"""Explicit renewal preflight, frozen confirmation and read-only reconciliation."""
import uuid

from django.db import transaction
from django.utils import timezone

from . import service as s
from .models import SSLSource, SSLCertificate, SSLJob


ACKS = {'ssh_renew': '仅续签所选证书订单，复用当前验证方式和凭据',
        'service_reload': '确认所列网站配置检查及固定 Web 服务重载影响',
        'ssl_panel_sync': '确认宝塔 SSL 面板同步及所列网站证书部署影响'}


def authorized(job, lock=False):
    """Fresh authorization and source identity before each remote boundary."""
    user = s.permission(job.owner_id, lock=lock)
    if not job.source_keys or len(job.source_keys) != 1:
        raise ValueError('续签来源无效')
    key = job.source_keys[0]
    kind, obj = s.source_object(key, lock=lock)
    qs = SSLSource.objects.select_for_update() if lock else SSLSource.objects
    pref = qs.filter(pk=key, enabled=True).first()
    if pref is None:
        raise s.WorkflowError('source_disabled')
    s.permission(pref.owner_id, lock=lock)
    version = job.source_versions.get(key, {})
    if pref.revision != version.get('revision') or s.config_digest(kind, obj) != version.get('digest'):
        raise s.WorkflowError('stale_plan')
    if s.server_key(key) != job.server_key:
        raise s.WorkflowError('stale_plan')
    return user, kind, obj, pref


def prepare(user, data):
    s.permission(user)
    s.strict(data, ('certificate_id',))
    with transaction.atomic():
        current = s.permission(user, lock=True)
        cert = SSLCertificate.objects.filter(
            pk=s.integer(data.get('certificate_id')), present=True).first()
        if cert is None:
            raise ValueError('证书记录不存在，请重新检查')
        kind, obj = s.source_object(cert.source_id, lock=True)
        pref = SSLSource.objects.select_for_update().get(pk=cert.source_id)
        # Match source configuration writers: source object, preference, then
        # certificate. Joined FOR UPDATE would lock the preference too early.
        cert = SSLCertificate.objects.select_for_update().get(pk=cert.pk, source_id=pref.pk, present=True)
        status, _ = s.source_status(kind, obj, pref)
        if status != 'ok' or cert.config_digest != s.config_digest(kind, obj):
            raise ValueError('证书记录已过期或来源已变化，请先重新检查')
        capability = s.certificate_renewal(cert)
        if not capability['supported']:
            raise ValueError(capability['reason'] or s.MESSAGES['unsupported_renewal'])
        server = s.server_key(cert.source_id)
        # Stable source/server row and durable lease serialize API admissions.
        existing = SSLJob.objects.filter(kind='renew', server_key=server, state__in=s.ACTIVE)
        if existing.exists():
            raise ValueError('该服务器已有续签任务或待核实意图，请先处理原任务')
        job = SSLJob.objects.create(owner=current, kind='renew', phase='preflight', certificate=cert,
            source_keys=[cert.source_id], server_key=server,
            source_versions={cert.source_id: {'revision': pref.revision, 'digest': s.config_digest(kind, obj)}},
            baseline={'certificate': s.safe_certificate(cert.local_certificate), 'cert_path': cert.cert_path,
                      'site_id': cert.site_id, 'domain': cert.domain, 'port': cert.port})
        s.reserve('renew:' + server, job, timezone.now())
        cert.latest_job = job
        cert.save(update_fields=['latest_job'])
        return s.job_view(job)


def start(user, job_id, data):
    s.strict(data, ('revision', 'acknowledgements'))
    with transaction.atomic():
        job = s.owned_job(user, job_id, lock=True)
        if job.kind != 'renew' or job.state != 'prepared' or job.intent:
            raise ValueError('任务尚未完成预检或已经提交，请刷新')
        if data.get('revision') != job.revision:
            raise ValueError('预检版本已变化，请刷新后确认')
        codes = data.get('acknowledgements')
        required = [ack['code'] for ack in job.acknowledgements]
        if not isinstance(codes, list) or any(not isinstance(c, str) for c in codes) or sorted(codes) != sorted(required):
            raise ValueError('请明确确认全部预检影响')
        authorized(job, lock=True)
        if s.digest(job.plan) != job.plan_digest:
            raise ValueError('冻结续签计划无法验证，请重新预检')
        job.state, job.phase, job.message = 'queued', 'dispatch', '已确认，等待后台重读配置后执行'
        job.revision = uuid.uuid4().hex
        job.save()
        return s.job_view(job)


def verify(user, job_id, data):
    s.strict(data, ())
    with transaction.atomic():
        job = s.owned_job(user, job_id, lock=True)
        if job.kind != 'renew' or job.state not in ('uncertain', 'success', 'verified', 'failed'):
            raise ValueError('仅已执行或待核实的续签任务可进行只读核验')
        authorized(job, lock=True)
        job.verify_requested = True
        job.state, job.phase = 'queued', 'verify'
        job.message = '等待只读核验，不会重新发送续签'
        job.revision = uuid.uuid4().hex
        job.save()
        return s.job_view(job)


def cancel_prepared(job, message):
    """Caller locks the job; cancelling unused consent never contacts a source."""
    if job.kind != 'renew' or job.state != 'prepared' or job.intent:
        raise ValueError('仅未下发的已预检任务可取消；待核实意图只能只读核验')
    if job.panel_task_id:
        from apps.baota.models import PanelTask
        PanelTask.objects.filter(pk=job.panel_task_id).update(cancel_requested=True)
    job.state, job.phase, job.message = 'cancelled', 'finished', message
    job.revision = uuid.uuid4().hex
    job.save()
    s.release(job)
    return s.job_view(job)


def cancel(user, job_id, data):
    s.strict(data, ())
    with transaction.atomic():
        job = s.owned_job(user, job_id, lock=True)
        return cancel_prepared(job, '已取消未下发的预检，可重新创建续签任务')


def panel_runtime_authorized(panel_task_id, mutating=True):
    """Existing PanelWorker hook: ordinary tasks pass; linked SSL tasks fail closed."""
    job = SSLJob.objects.filter(panel_task_id=panel_task_id).first()
    if job is None:
        from apps.baota.models import PanelTask
        task = PanelTask.objects.filter(pk=panel_task_id).first()
        return bool(task and not any(a.get('action') == 'ssl_monitor' for a in task.audit))
    try:
        phases = ('panel_wait',) if mutating else ('panel_preflight', 'awaiting_confirmation', 'panel_wait')
        if job.kind != 'renew' or job.state not in ('queued', 'running', 'prepared') or job.phase not in phases or mutating and not job.intent:
            return False
        if job.state == 'running' and job.lease_until and job.lease_until <= timezone.now():
            return False
        authorized(job)
        return True
    except Exception:
        return False
