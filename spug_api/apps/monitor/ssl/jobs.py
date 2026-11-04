"""Bounded database worker. Runs independently of the existing panel tick."""
import copy
import time
import uuid
from datetime import timedelta

from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from . import service as s
from . import renewal
from .models import SSLSource, SSLCertificate, SSLJob, SSLLease, SSLReminder


SOURCE_BUDGET = 120
CHECK_BUDGET = 480
DISCOVERY_MARGIN = 45  # collector child hard deadline is 40 seconds
TLS_MARGIN = 20  # TLS child hard deadline is 15 seconds
MAX_SNAPSHOT_BYTES = 1024 * 1024


def discover(kind, source):
    from .collectors import discover as collect
    return collect(kind, source)


def check_tls(domain, port):
    from .tls import check_tls as check
    return check(domain, port)


def renew_host(host, plan):
    from .collectors import renew_host as renew
    return renew(host, plan)


def after_intent(job):
    """Intent boundary, kept separate so restart/authorization races can be tested."""


def finish(job, state, message, result=None, phase='finished'):
    expected_state, expected_token = job.state, job.lease_token
    job.state, job.phase, job.message = state, phase, message
    job.revision = uuid.uuid4().hex
    job.lease_token, job.lease_until = '', None
    if result is not None:
        job.result = result
    # A recovered or superseded worker cannot overwrite its successor, even
    # when an old remote call eventually returns after its lease expired.
    fields = ('state', 'phase', 'message', 'revision', 'lease_token', 'lease_until', 'result',
              'plan', 'plan_digest', 'summary', 'acknowledgements', 'verify_requested', 'panel_task_id')
    changed = SSLJob.objects.filter(pk=job.pk, state=expected_state, lease_token=expected_token).update(
        **{field: getattr(job, field) for field in fields}, updated_at=timezone.now())
    if not changed:
        return False
    if state in ('success', 'verified', 'failed', 'blocked', 'cancelled'):
        s.release(job)
    return True


def recover_expired():
    """Never replay persisted mutation intent after worker loss."""
    now = timezone.now()
    cutoff = now - timedelta(minutes=30)
    for job_id in SSLJob.objects.filter(kind='renew', state='prepared', intent={}, updated_at__lte=cutoff).values_list('id', flat=True):
        with transaction.atomic():
            job = SSLJob.objects.select_for_update().get(pk=job_id)
            if job.state == 'prepared' and not job.intent and job.updated_at <= cutoff:
                renewal.cancel_prepared(job, '只读预检已超过 30 分钟，确认已过期；请重新创建预检')
    for job_id in SSLJob.objects.filter(state='running', lease_until__lte=now).values_list('id', flat=True):
        with transaction.atomic():
            job = SSLJob.objects.select_for_update().get(pk=job_id)
            if job.state != 'running' or not job.lease_until or job.lease_until > now:
                continue
            if job.intent:
                finish(job, 'uncertain', '执行过程被中断，结果待核实；仅允许只读核验')
            elif job.kind == 'renew':
                finish(job, 'queued', '只读预检被中断，等待重新预检', phase='preflight')
            else:
                SSLSource.objects.filter(pk__in=job.source_keys).update(status='stale', message='检查中断，请重新检查')
                finish(job, 'failed', '检查中断，请重新检查')


def safe_rows(payload):
    import json
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise ValueError('来源数据无效')
    if len(payload['items']) > s.MAX_CERTIFICATES or len(json.dumps(payload).encode()) > MAX_SNAPSHOT_BYTES:
        raise s.WorkflowError('output_limit')
    rows, seen = [], set()
    for raw in payload['items']:
        if not isinstance(raw, dict):
            raise ValueError('来源数据无效')
        domain, port = s.domain_name(raw.get('domain')), raw.get('port')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('来源端口无效')
        site = str(raw.get('site_id', ''))
        name, path = raw.get('site_name', ''), raw.get('cert_path', '')
        if not site or len(site) > 512 or any(ord(c) < 32 for c in site) or not isinstance(name, str) or len(name) > 255:
            raise ValueError('来源网站身份无效')
        if not isinstance(path, str) or len(path) > 2048 or any(ord(c) < 32 for c in path):
            raise ValueError('证书路径无效')
        identity = s.digest([site, domain, port])
        if identity in seen:
            raise ValueError('来源包含重复证书记录')
        seen.add(identity)
        renew = s.safe_renewal(raw.get('renewal'))
        plan = raw.get('renewal', {}).get('plan', {})
        if not isinstance(plan, dict) or len(json.dumps(plan).encode()) > 32768:
            raise ValueError('续签计划超出限制')
        # Collectors only emit a fixed public-identity plan. No raw configuration,
        # credentials, PEM or private material may cross into persistence.
        def clean_plan(value):
            if isinstance(value, dict):
                if any(str(k).lower() in ('password', 'pkey', 'private_key', 'api_key', 'config', 'config_text', 'pem', 'credentials', 'token') for k in value):
                    raise ValueError('续签计划包含非公开配置')
                for item in value.values():
                    clean_plan(item)
            elif isinstance(value, list):
                for item in value:
                    clean_plan(item)
            elif isinstance(value, str) and ('-----BEGIN' in value or '\n' in value or '\r' in value):
                raise ValueError('续签计划包含非公开配置')
        clean_plan(plan)
        rows.append({'identity': identity, 'site_id': site, 'site_name': name, 'domain': domain, 'port': port,
            'cert_path': path, 'local_certificate': s.safe_certificate(raw.get('certificate')),
            'deployment_verified': raw.get('deployment_verified') is True,
            'renewal': renew, 'plan': copy.deepcopy(plan)})
    warnings = payload.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > 100:
        raise ValueError('来源覆盖诊断无效')
    return rows, bool(warnings)


def source_authorized(job, key, lock=False):
    if not lease_valid(job):
        raise s.WorkflowError('lease_expired')
    s.permission(job.owner_id, lock=lock)
    kind, obj = s.source_object(key, lock=lock)
    qs = SSLSource.objects.select_for_update() if lock else SSLSource.objects
    pref = qs.filter(pk=key, enabled=True).first()
    if pref is None:
        raise s.WorkflowError('source_disabled')
    s.permission(pref.owner_id, lock=lock)
    expected = job.source_versions[key]
    if pref.revision != expected['revision'] or s.config_digest(kind, obj) != expected['digest']:
        raise s.WorkflowError('stale_plan')
    return kind, obj, pref


def lease_valid(job):
    return SSLJob.objects.filter(pk=job.pk, state='running', lease_token=job.lease_token,
                                 lease_until__gt=timezone.now()).exists()


def collect_source(job, key, deadline=None):
    start = time.monotonic()
    deadline = min(deadline if deadline is not None else start + SOURCE_BUDGET, start + SOURCE_BUDGET)
    kind, obj, pref = source_authorized(job, key)
    if deadline - time.monotonic() < DISCOVERY_MARGIN:
        raise TimeoutError()
    rows, warning = safe_rows(discover(kind, obj))
    for row in rows:
        source_authorized(job, key)
        if deadline - time.monotonic() < TLS_MARGIN or not lease_valid(job):
            raise TimeoutError()
        row['public_tls'] = ({'verified': False, 'certificate': {}, 'message': '通配符仅检查本地部署证书'}
                             if row['domain'].startswith('*.') else s.safe_tls(check_tls(row['domain'], row['port'])))
    if time.monotonic() > deadline:
        raise TimeoutError()
    with transaction.atomic():
        _, _, pref = source_authorized(job, key, lock=True)
        if not lease_valid(job):
            raise ValueError('检查租约已过期')
        now = timezone.now()
        SSLCertificate.objects.filter(source=pref).update(present=False)
        for row in rows:
            cert, _ = SSLCertificate.objects.update_or_create(source=pref, identity=row['identity'], defaults={
                **row, 'sampled_at': now, 'config_digest': job.source_versions[key]['digest'], 'present': True})
            if pref.reminders:
                create_reminder(cert)
        pref.last_success_at, pref.config_digest = now, job.source_versions[key]['digest']
        pref.status, pref.message = 'ok', '检查完成；部分配置覆盖不足，请查看来源工具指导' if warning else '检查完成'
        pref.save()
    return {'source_key': key, 'state': 'success', 'count': len(rows), 'coverage_incomplete': warning}


def create_reminder(cert):
    local = cert.local_certificate
    if not cert.deployment_verified or not local or not local.get('expires_at'):
        return
    remaining = (s.instant(local['expires_at']) - s.utcnow()).total_seconds() / 86400
    stage = 'expired' if remaining <= 0 else next((str(v) for v in (1, 3, 7, 10, 30) if remaining <= v), None)
    if stage:
        SSLReminder.objects.get_or_create(certificate=cert, cycle=s.certificate_cycle(cert), stage=stage,
                                          defaults={'expires_at': local['expires_at']})


def run_check(job):
    result = []
    deadline = time.monotonic() + CHECK_BUDGET
    for index, key in enumerate(job.source_keys):
        # Reserve the full source budget plus the longest network-call margin.
        # No new source is contacted when it could outlive the global lease.
        if deadline - time.monotonic() < SOURCE_BUDGET + DISCOVERY_MARGIN:
            for remaining in job.source_keys[index:]:
                SSLSource.objects.filter(pk=remaining).update(status='stale', message=s.MESSAGES['timeout'])
                result.append({'source_key': remaining, 'state': 'failed', 'message': s.MESSAGES['timeout']})
            break
        try:
            result.append(collect_source(job, key, deadline=deadline))
        except PermissionError:
            finish(job, 'cancelled', '账号权限已撤销，后续检查已停止')
            return
        except Exception as error:
            SSLSource.objects.filter(pk=key).update(status='stale', message=s.safe_error(error))
            result.append({'source_key': key, 'state': 'failed', 'message': s.safe_error(error)})
    failed = any(r['state'] == 'failed' for r in result)
    finish(job, 'failed' if failed else 'success', '部分来源检查未完成' if failed else '检查完成', {'sources': result})


def selected_row(job, rows):
    cert = job.certificate
    return next((r for r in rows if r['identity'] == cert.identity), None)


def preflight(job):
    if not lease_valid(job):
        return
    user, kind, obj, pref = renewal.authorized(job)
    rows, _ = safe_rows(discover(kind, obj))
    renewal.authorized(job)
    row = selected_row(job, rows)
    if not row or not row['deployment_verified'] or not row['renewal']['supported'] or not row['local_certificate']:
        finish(job, 'blocked', s.MESSAGES['unsupported_renewal'])
        return
    if row['cert_path'] != job.baseline.get('cert_path') or row['local_certificate']['fingerprint'] != job.baseline['certificate']['fingerprint']:
        finish(job, 'blocked', s.MESSAGES['stale_plan'])
        return
    job.plan = row['plan']
    job.plan_digest = s.digest(row['plan'])
    job.summary = {**s.safe_summary(row['renewal']['summary']), 'source': obj.name,
                   'domain': row['domain'], 'cert_path': row['cert_path'], 'engine': row['renewal']['engine']}
    if kind == 'panel':
        from apps.baota import task_service
        # Only the selected site is admitted; the original adapter resolves the
        # current matching order and lists all panel synchronization effects.
        site_id = s.integer(row['site_id'])
        with transaction.atomic():
            renewal.authorized(job, lock=True)
            if not lease_valid(job):
                return
            task = task_service.prepare(user, {'operation': 'ssl_renew', 'panel_ids': [obj.pk], 'params': {'sites': [site_id]}})
            task.audit = [*task.audit, {'action': 'ssl_monitor', 'job_id': job.pk}]
            task.save(update_fields=['audit', 'updated_at'])
            job.panel_task_id = task.pk
            if not finish(job, 'queued', '原宝塔任务正在进行只读预检；尚未授权执行', phase='panel_preflight'):
                raise ValueError('任务租约已变化')
        return
    codes = ('ssh_renew', 'service_reload')
    job.acknowledgements = [{'code': code, 'label': renewal.ACKS[code]} for code in codes]
    finish(job, 'prepared', '只读预检完成，请确认全部影响后开始', phase='awaiting_confirmation')


def sync_panel_preflight(job):
    from apps.baota import task_service
    renewal.authorized(job)
    task = job.panel_task
    task.refresh_from_db()
    if task.state == 'prepared':
        targets = list(task.targets.all())
        if not any(c['state'] == 'ready' for t in targets for c in t.children):
            finish(job, 'blocked', '宝塔任务未找到可续签的已选订单，请查看原任务诊断')
            return
        required = task_service.required_acks(targets)
        if any(c not in renewal.ACKS for c in required):
            finish(job, 'blocked', '原任务确认项无法安全解析')
            return
        job.acknowledgements = [{'code': code, 'label': renewal.ACKS[code]} for code in required]
        finish(job, 'prepared', '宝塔只读预检完成，请确认面板同步影响', phase='awaiting_confirmation')
    elif task.state in ('failed', 'cancelled', 'success', 'completed', 'uncertain'):
        finish(job, 'blocked', '宝塔预检未产生可执行订单，请查看原任务')
    else:
        finish(job, 'queued', '原宝塔任务正在进行只读预检；尚未授权执行', phase='panel_preflight')


def dispatch_renewal(job):
    if not lease_valid(job):
        return
    if job.intent:
        finish(job, 'uncertain', '续签已存在持久化意图，仅允许只读核验')
        return
    user, kind, obj, pref = renewal.authorized(job)
    rows, _ = safe_rows(discover(kind, obj))
    row = selected_row(job, rows)
    if not row or not row['deployment_verified'] or s.digest(row['plan']) != job.plan_digest or row['cert_path'] != job.baseline['cert_path']:
        finish(job, 'blocked', s.MESSAGES['stale_plan'])
        return
    if row['local_certificate'].get('fingerprint') != job.baseline['certificate'].get('fingerprint'):
        finish(job, 'blocked', '证书在确认后已经变化，请重新检查并预检')
        return
    with transaction.atomic():
        renewal.authorized(job, lock=True)
        if not lease_valid(job):
            raise ValueError('任务租约已过期')
        job.intent = {'at': s.iso(timezone.now()), 'plan_digest': job.plan_digest}
        job.save(update_fields=['intent', 'updated_at'])
    after_intent(job)
    # Re-read immediately at the actual send boundary, after committing intent.
    user, kind, obj, _ = renewal.authorized(job)
    if not lease_valid(job):
        raise ValueError('任务租约已过期')
    if kind == 'panel':
        from apps.baota import task_service
        with transaction.atomic():
            renewal.authorized(job, lock=True)
            finish(job, 'queued', '原宝塔续签任务已确认，等待逐项执行', phase='panel_wait')
            task_service.start(user, job.panel_task_id, {'acknowledgements': [a['code'] for a in job.acknowledgements]})
        return
    outcome = renew_host(obj, copy.deepcopy(job.plan))
    if isinstance(outcome, dict) and outcome.get('state') == 'unchanged':
        finish(job, 'failed', '客户端已结束但未更新证书，可能尚未达到续签窗口；请稍后重新检查',
               {'local_updated': False, 'public_updated': False, 'reload_verified': False})
        return
    if not isinstance(outcome, dict) or outcome.get('state') != 'success':
        finish(job, 'uncertain', '续签调用已结束但部署结果尚未确认，仅允许只读核验')
        return
    # Tool outcome is insufficient: independently discover deployed public
    # certificates and perform public SNI verification before final success.
    verify_renewal(job, reload_verified=True)


def verify_renewal(job, reload_verified=False):
    if not lease_valid(job):
        return
    _, kind, obj, _ = renewal.authorized(job)
    rows, _ = safe_rows(discover(kind, obj))
    renewal.authorized(job)
    if not lease_valid(job):
        return
    row = selected_row(job, rows)
    if not row or not row['deployment_verified'] or row['cert_path'] != job.baseline['cert_path'] or not row['local_certificate']:
        finish(job, 'uncertain', '未能核实原证书部署位置，需人工检查')
        return
    local, before = row['local_certificate'], job.baseline['certificate']
    changed = (local['fingerprint'] != before.get('fingerprint') and
               s.instant(local['expires_at']) > s.instant(before['expires_at']))
    public = ({'verified': False, 'certificate': {}, 'message': '通配符仅检查本地部署证书'} if row['domain'].startswith('*.')
              else s.safe_tls(check_tls(row['domain'], row['port'])))
    reload_confirmed = changed and (reload_verified or job.result.get('reload_verified') is True)
    public_updated = bool(changed and public.get('verified') and
                          public.get('certificate', {}).get('fingerprint') == local['fingerprint'])
    result = {'local_updated': changed, 'public_updated': public_updated, 'reload_verified': reload_confirmed,
              'local_certificate': local, 'public_tls': public}
    with transaction.atomic():
        renewal.authorized(job, lock=True)
        if not lease_valid(job):
            return
        cert = SSLCertificate.objects.select_for_update().get(pk=job.certificate_id)
        cert.local_certificate, cert.public_tls, cert.sampled_at = local, public, timezone.now()
        cert.save(update_fields=['local_certificate', 'public_tls', 'sampled_at'])
        if reload_confirmed and public_updated:
            finish(job, 'success', '已核实本地部署与公网证书更新', result)
        elif changed and public_updated:
            finish(job, 'verified', '已核实本地与公网证书更新，服务重载过程未获确认；未重新续签', result)
        else:
            finish(job, 'uncertain', '本地证书已更新，公网证书尚未生效或需核对 CDN' if changed else
                   '只读核验尚未证明证书更新及服务重载成功，请人工核对', result)


def sync_panel_execution(job):
    renewal.authorized(job)
    task = job.panel_task
    task.refresh_from_db()
    targets = list(task.targets.all())
    completed = task.state == 'completed' and bool(targets) and all(
        target.state == 'completed' and bool(target.children) and
        all(child.get('state') in ('success', 'already') for child in target.children) for target in targets)
    if completed:
        verify_renewal(job, reload_verified=True)
    elif task.state in ('completed', 'failed', 'cancelled', 'uncertain'):
        finish(job, 'uncertain', '原宝塔任务未确认全部部署成功，请查看原任务并只读核验')
    else:
        finish(job, 'queued', '原宝塔续签任务等待逐项执行及部署核验', phase='panel_wait')


def schedule_due():
    if SSLJob.objects.filter(kind='check', state__in=('queued', 'running')).exists():
        return
    now = timezone.now()
    for pref in SSLSource.objects.filter(enabled=True).order_by('last_attempt_at', 'key'):
        if pref.last_attempt_at and now - pref.last_attempt_at < timedelta(hours=pref.interval_hours):
            continue
        try:
            owner = s.permission(pref.owner_id)
            s.enqueue_checks(owner, {'source_keys': [pref.key]})
            return
        except (ValueError, PermissionError):
            continue


def run_pending():
    """One leased unit per scheduler tick; no network work in API processes."""
    recover_expired()
    # Fresh deployment must leave all six additive tables empty until an
    # administrator explicitly configures a source or creates work.
    if not SSLSource.objects.filter(enabled=True).exists() and not SSLJob.objects.filter(
            Q(state='queued') | Q(verify_requested=True)).exists():
        return False
    now = timezone.now()
    try:
        with transaction.atomic():
            token = s.reserve('worker', None, now)
    except ValueError:
        return False
    try:
        schedule_due()
        ids = list(SSLJob.objects.filter(Q(state='queued') | Q(verify_requested=True)).order_by('updated_at', 'id').values_list('id', flat=True)[:100])
        for job_id in ids:
            with transaction.atomic():
                job = SSLJob.objects.select_for_update().get(pk=job_id)
                if job.state == 'running' or job.state != 'queued' and not job.verify_requested:
                    continue
                verify = job.verify_requested
                job.verify_requested = False
                job.state, job.lease_token, job.lease_until = 'running', token, now + timedelta(seconds=s.LEASE_SECONDS)
                job.save()
            try:
                s.permission(job.owner_id)
                if verify:
                    verify_renewal(job)
                elif job.kind == 'check':
                    run_check(job)
                elif job.phase == 'preflight':
                    preflight(job)
                elif job.phase == 'panel_preflight':
                    sync_panel_preflight(job)
                elif job.phase == 'panel_wait':
                    sync_panel_execution(job)
                elif job.phase == 'dispatch':
                    dispatch_renewal(job)
                else:
                    finish(job, 'blocked', '任务阶段无法验证，请重新预检')
            except PermissionError:
                finish(job, 'uncertain' if job.intent else 'cancelled', '账号权限已撤销，后续操作已停止')
            except Exception as error:
                finish(job, 'uncertain' if job.intent else 'blocked', s.safe_error(error))
            return True
        return False
    finally:
        SSLLease.objects.filter(pk='worker', token=token).update(expires_at=timezone.now(), token='')


def scheduled_tick():
    try:
        return run_pending()
    finally:
        connections.close_all()
