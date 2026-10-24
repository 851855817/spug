"""One durable file per queue step; all network calls occur outside transactions."""
import hashlib
import posixpath

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from . import task_service as s, upload_store as store
from .models import PanelUploadResult
from . import resources
from .operations.base import child
from .operations.uploads import RemotePaths, destination, verify_content


def summary(target):
    counts = dict(target.upload_files.values('state').annotate(n=Count('pk')).values_list('state', 'n'))
    bundle = target.upload_binding.bundle
    return {'files': bundle.file_count, 'total_bytes': bundle.total_bytes,
            'success': counts.get('success', 0), 'failed': counts.get('failed', 0),
            'uncertain': counts.get('uncertain', 0), 'cancelled': counts.get('cancelled', 0),
            'pending': counts.get('ready', 0) + counts.get('dispatching', 0),
            'target_dir': target.params['target_dir'], 'directory_source': target.params['directory_source']}


def sync(target, prepared=False):
    """Caller owns task and target row locks, then file rows. Never reverse this order."""
    progress = summary(target)
    if progress['uncertain']:
        state, message = 'uncertain', '部分文件待核实；不会自动重发'
    elif progress['pending']:
        state, message = 'ready', '预检完成；尚未授权执行' if prepared else '等待上传剩余文件'
    elif progress['failed']:
        state, message = 'failed', '上传已结束，部分文件失败'
    elif progress['cancelled'] and not progress['success']:
        state, message = 'cancelled', '未发送文件已取消'
    else:
        state, message = 'success', '全部已发送文件内容校验通过'
    item = target.children[0]
    item['state'] = state
    item['result'] = {'state': state, 'message': message, 'evidence': progress}
    target.summary = {**s.preflight_summary(target.children), 'upload': progress}
    target.message = message
    if prepared:
        target.state = 'prepared'
        target.save(update_fields=['state', 'children', 'summary', 'message', 'updated_at'])
    else:
        s.settle_target(target)
        target.save(update_fields=['summary', 'updated_at'])


def cancel_unsent(target):
    target.upload_files.filter(state='ready').update(state='cancelled', message='已取消未发送文件', updated_at=timezone.now())


def preflight(worker, target):
    from .worker import locked_target, WorkerStopping
    bundle = target.upload_binding.bundle
    rows, info = [], {'version': target.panel.api_version}
    try:
        s.configured(target.panel, target.panel_snapshot['api_config_revision'])
        if bundle.state != 'sealed':
            raise ValueError('源文件包不可用')
        with worker.client(target.panel) as client:
            info = resources.system_info(client)
            paths = RemotePaths(client)
            for source in bundle.files.order_by('id'):
                try:
                    store.blob_path(source)
                    before = paths.inspect(destination(target.params['target_dir'], source))
                    rows.append(PanelUploadResult(target=target, source=source,
                        before={**before, 'panel_version': info['version']}, message='预检通过'))
                except (ValueError,):
                    rows.append(PanelUploadResult(target=target, source=source, state='failed',
                        message='目标路径冲突、符号链接或源文件不可用'))
    except WorkerStopping:
        with transaction.atomic():
            task, current = locked_target(target.pk)
            current.state = 'preflight_queued'
            current.save(update_fields=['state', 'updated_at'])
            s.refresh_task(task)
        return
    except Exception:
        rows = [PanelUploadResult(target=target, source=source, state='failed',
                  message='预检失败，请核对目录访问权限、API 配置和源文件') for source in bundle.files.all()]
    with transaction.atomic():
        task, current = locked_target(target.pk)
        if current.state != 'preflighting':
            return
        current.upload_files.all().delete()  # Only a never-dispatched read-only preflight can be retried.
        PanelUploadResult.objects.bulk_create(rows)
        item = child('upload', '上传文件 / 目录', {'target_dir': current.params['target_dir'],
            'directory_source': current.params['directory_source']}, info,
            plan={'bundle_id': bundle.pk, 'manifest_sha256': bundle.manifest_sha256,
                  'file_count': bundle.file_count, 'total_bytes': bundle.total_bytes,
                  'required_acknowledgements': ['upload_overwrite']},
            before={'new_files': sum(r.state == 'ready' and not r.before.get('exists') for r in rows),
                    'overwrite_files': sum(r.state == 'ready' and r.before.get('exists', False) for r in rows)})
        if not any(row.state == 'ready' for row in rows):
            item['state'], item['message'] = 'failed', '没有通过预检的文件'
        current.children = [{'snapshot': item, 'state': item['state'], 'intents': [], 'result': {}, 'resolutions': []}]
        if task.cancel_requested:
            cancel_unsent(current)
        sync(current, prepared=not task.cancel_requested)
        s.refresh_task(task)


def process(worker, target):
    from .worker import locked_target
    if target.state == 'preflighting':
        return preflight(worker, target)
    if target.state != 'running':
        return
    # Uncertain files block later sends, even after a cancel request.
    row = target.upload_files.select_related('source').filter(state='uncertain').order_by('source_id').first()
    if row:
        return readback(worker, target, row)
    row = target.upload_files.select_related('source').filter(state='ready').order_by('source_id').first()
    if row is None:
        with transaction.atomic():
            task, current = locked_target(target.pk)
            sync(current)
            s.refresh_task(task)
        return
    # Validate source integrity and current remote identity BEFORE the write intent.
    try:
        s.configured(target.panel, target.panel_snapshot['api_config_revision'])
        with store.blob_path(row.source).open('rb') as stream:
            digest, length = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
                length += len(chunk)
            if digest.hexdigest() != row.source.sha256 or length != row.source.size:
                raise ValueError('源文件摘要变化')
            filename = destination(target.params['target_dir'], row.source)
            with worker.client(target.panel) as client:
                info = resources.system_info(client)
                current_before = RemotePaths(client).inspect(filename)
                if (info['version'] != row.before.get('panel_version')
                        or current_before != {k: row.before[k] for k in ('exists', 'size', 'mtime')}):
                    raise ValueError('目标文件在预检后变化')
                if not begin(worker, target.pk, row.pk):
                    return
                stream.seek(0)
                # No automatic retry, redirect or fallback after this point.
                try:
                    client.upload_file(posixpath.dirname(filename), posixpath.basename(filename), stream)
                    evidence = verify_content(client, filename, row.source)
                    result = {'state': 'success', 'message': '上传完成，内容校验通过', 'evidence': evidence}
                except Exception:
                    result = {'state': 'uncertain', 'message': '上传结果待核实；不会自动重发', 'evidence': {}}
        return finish(target.pk, row.pk, result)
    except Exception:
        # No write intent was persisted. Restart can safely repeat only these reads.
        with transaction.atomic():
            task, current = locked_target(target.pk)
            saved = current.upload_files.select_for_update().get(pk=row.pk)
            if saved.state == 'ready':
                if worker.stop_event.is_set():
                    sync(current)
                    s.refresh_task(task)
                    return
                saved.state, saved.message = 'failed', '源文件、API 配置或远端路径在预检后变化，请重新预检'
                saved.save(update_fields=['state', 'message', 'updated_at'])
            elif saved.state == 'dispatching':
                saved.state, saved.message = 'uncertain', '调用中断，结果待核实；不会自动重发'
                saved.save(update_fields=['state', 'message', 'updated_at'])
            if task.cancel_requested:
                cancel_unsent(current)
            sync(current)
            s.refresh_task(task)


def begin(worker, target_id, result_id):
    from .worker import locked_target
    with transaction.atomic():
        task, target = locked_target(target_id)
        row = target.upload_files.select_for_update().get(pk=result_id)
        if target.state != 'running' or row.state != 'ready' or row.intent:
            return False
        if task.execution_policy and not s.batch_allows(task, target):
            # A concurrent panel can trip the batch while this upload performs
            # bounded pre-write reads. Recheck under task -> target -> file locks.
            s.settle_target(target)
            s.refresh_task(task)
            return False
        if worker.stop_event.is_set() or task.cancel_requested:
            if task.cancel_requested:
                cancel_unsent(target)
            sync(target)
            s.refresh_task(task)
            return False
        s.configured(target.panel, target.panel_snapshot['api_config_revision'])
        if 'upload_overwrite' not in task.acknowledgements:
            raise ValueError('缺少覆盖确认')
        bundle = target.upload_binding.bundle
        if (bundle.state != 'sealed' or bundle.manifest_sha256 != target.children[0]['snapshot']['plan']['manifest_sha256']):
            raise ValueError('源文件包发生变化')
        row.intent = {'at': s.now(), 'api_config_revision': target.panel.api_config_revision,
                      'target_dir': target.params['target_dir'], 'manifest_sha256': bundle.manifest_sha256}
        row.state = 'dispatching'
        row.save(update_fields=['state', 'intent', 'updated_at'])
        target.state = 'dispatching'
        target.save(update_fields=['state', 'updated_at'])
        s.refresh_task(task)
        return True


def finish(target_id, result_id, result):
    from .worker import locked_target
    with transaction.atomic():
        task, target = locked_target(target_id)
        row = target.upload_files.select_for_update().get(pk=result_id)
        row.state, row.message, row.result = result['state'], result['message'], result
        row.save(update_fields=['state', 'message', 'result', 'updated_at'])
        target.reconcile_requested = False
        if task.cancel_requested:
            cancel_unsent(target)
        sync(target)
        s.refresh_task(task)


def readback(worker, target, row):
    from .worker import locked_target
    with transaction.atomic():
        task, current = locked_target(target.pk)
        current.state, current.reconcile_requested = 'reconciling', False
        current.save(update_fields=['state', 'reconcile_requested', 'updated_at'])
    try:
        s.configured(target.panel, target.panel_snapshot['api_config_revision'])
        filename = destination(target.params['target_dir'], row.source)
        with worker.client(target.panel) as client:
            if resources.system_info(client)['version'] != row.before['panel_version']:
                raise ValueError('面板版本变化')
            RemotePaths(client).inspect(filename)
            evidence = verify_content(client, filename, row.source)
        result = {'state': 'success', 'message': '只读核验确认文件内容一致', 'evidence': evidence}
    except Exception:
        result = {'state': 'uncertain', 'message': '暂时无法确认文件内容，请继续核验或人工处理', 'evidence': {}}
    finish(target.pk, row.pk, result)


def recover(target):
    target.upload_files.filter(state='dispatching').update(state='uncertain',
        message='Worker 中断，结果待核实；不会自动重发', updated_at=timezone.now())
    sync(target)


def resolve_file(user, task, target, data):
    if not data['child_key'].startswith('file:'):
        raise ValueError('上传任务必须逐文件核验')
    try:
        row = target.upload_files.select_for_update().get(source_id=data['child_key'][5:], state='uncertain')
    except PanelUploadResult.DoesNotExist:
        raise ValueError('仅可处理待核实文件') from None
    resolution = {'actor_id': user.pk, 'at': s.now(), **{k: data[k] for k in ('outcome', 'reason', 'evidence')}}
    row.resolutions = s.bounded([*row.resolutions, resolution])
    row.state, row.message = data['outcome'], '管理员依据人工核验作出结论'
    row.result = {'state': row.state, 'message': row.message, 'evidence': {'manual_resolution': resolution}}
    row.save(update_fields=['state', 'message', 'result', 'resolutions', 'updated_at'])
    if task.cancel_requested:
        cancel_unsent(target)
    sync(target)
    s.audit(task, user, 'resolve', target_id=target.pk, child_key=data['child_key'], **resolution)
    s.refresh_task(task)
