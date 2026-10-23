"""Owner-scoped private source storage. This module never contacts a panel.

Quota locks precede bundle locks. Receives hold bundle/file locks only while
claiming and finishing an attempt, never while consuming the uploaded stream.
"""
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import PanelUploadBundle, PanelUploadFile, PanelUploadQuota


_ID = re.compile(r'[0-9a-f]{32}\Z')
_BLOB = re.compile(r'[0-9a-f]{32}\.blob\Z')
_PART = re.compile(r'[0-9a-f]{32}\.[0-9a-f]{32}\.part\Z')
_ENDED = frozenset(('completed', 'success', 'failed', 'already', 'skipped', 'cancelled'))
_DAY = timedelta(hours=24)


def limits():
    result = dict(file_bytes=20 * 1024**2, bundle_bytes=200 * 1024**2, files=1000,
                  global_bytes=1024**3, user_bytes=400 * 1024**2, reserve_bytes=512 * 1024**2)
    overrides = getattr(settings, 'BAOTA_UPLOAD_LIMITS', {})
    if not isinstance(overrides, dict) or set(overrides) - set(result):
        raise ValueError('上传限制配置无效')
    result.update(overrides)
    if any(type(v) is not int or v < (0 if key == 'reserve_bytes' else 1) for key, v in result.items()):
        raise ValueError('上传限制配置无效')
    return result


def _path_parts(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('文件路径无效')
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) or any(c in value for c in ('\\', '~', '&', '|')):
        raise ValueError('文件路径包含无效字符')
    parts = value.split('/')
    try:
        if any(p in ('', '.', '..') or len(p.encode('utf-8')) > 255 for p in parts):
            raise ValueError('文件路径无效')
    except UnicodeError:
        raise ValueError('文件路径无效') from None
    return parts


def absolute_directory(value):
    if not isinstance(value, str) or not value.startswith('/') or value == '/' or len(value) > 1024:
        raise ValueError('请输入根目录以外的绝对目标目录')
    value = value[:-1] if value.endswith('/') else value
    _path_parts(value[1:])
    return value


def effective_directory(common, individual=None):
    if individual is not None and not isinstance(individual, str):
        raise ValueError('单独目标目录无效')
    if isinstance(individual, str) and individual.strip():
        return absolute_directory(individual.strip())
    if not isinstance(common, str):
        raise ValueError('统一目标目录无效')
    return absolute_directory(common.strip())


def relative_path(value):
    parts = _path_parts(value)
    # Portable source names also exclude drive letters, ADS and Windows special
    # components, even though all actual blob paths use independent random IDs.
    if any(re.search(r'[:<>"|?*]', p) or p.endswith((' ', '.')) or
           re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts):
        raise ValueError('文件路径不是可移植的相对路径')
    return value


def _permission(user):
    from .task_service import permission
    if user is None or not getattr(user, 'pk', None):
        raise PermissionError('权限拒绝')
    permission(user, 'create')


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PermissionError('文件包不存在或无权访问')
    return value


def _root():
    root = Path(getattr(settings, 'BAOTA_UPLOAD_ROOT', '/data/baota-uploads'))
    if not root.is_absolute():
        raise ValueError('上传私有存储配置无效')
    # Reject links in the configured path too, rather than following a link into
    # a web-served directory and treating its resolved destination as trusted.
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('上传私有存储路径无效')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root.resolve()


def _directory(bundle_id, create=False):
    root = _root()
    path = root / _identifier(bundle_id)
    if path.is_symlink():
        raise ValueError('上传私有存储路径无效')
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    if path.resolve().parent != root:
        raise ValueError('上传私有存储路径无效')
    return path


def _blob(source):
    path = _directory(source.bundle_id) / (_identifier(source.pk) + '.blob')
    if path.is_symlink():
        raise ValueError('上传源文件不可用')
    return path


def _disk_space(required):
    if shutil.disk_usage(_root())[2] < limits()['reserve_bytes'] + required:
        raise ValueError('私有暂存空间不足，请减少文件或稍后再试')


def _quota_lock():
    PanelUploadQuota.objects.get_or_create(pk=1)
    return PanelUploadQuota.objects.select_for_update().get(pk=1)


def create_bundle(user, files):
    _permission(user)
    cap = limits()
    if not isinstance(files, list) or not 1 <= len(files) <= cap['files']:
        raise ValueError('上传文件数量超出限制')
    manifest, seen, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'size'}:
            raise ValueError('上传清单格式无效')
        path, size = relative_path(item['path']), item['size']
        if type(size) is not int or size < 0 or size > cap['file_bytes']:
            raise ValueError('单个上传文件大小超出限制')
        if path in seen:
            raise ValueError('上传文件路径重复')
        seen.add(path)
        manifest.append((path, size))
        total += size
    if any('/'.join(path.split('/')[:n]) in seen for path in seen for n in range(1, len(path.split('/')))):
        raise ValueError('上传清单存在文件与目录同名冲突')
    if total > cap['bundle_bytes']:
        raise ValueError('上传文件包大小超出限制')
    with transaction.atomic():
        _quota_lock()
        active = PanelUploadBundle.objects.exclude(state='expired')
        global_bytes = active.aggregate(total=Sum('total_bytes'))['total'] or 0
        user_bytes = active.filter(owner_id=user.pk).aggregate(total=Sum('total_bytes'))['total'] or 0
        if global_bytes + total > cap['global_bytes'] or user_bytes + total > cap['user_bytes']:
            raise ValueError('上传暂存配额不足，请释放未使用的文件包')
        # Reserve all declared bytes, including pending files. Counting saved
        # bytes again is deliberately conservative when checking free disk.
        _disk_space(global_bytes + total)
        bundle = PanelUploadBundle.objects.create(owner=user, total_bytes=total, file_count=len(manifest))
        PanelUploadFile.objects.bulk_create([PanelUploadFile(bundle=bundle, relative_path=p, size=s,
            path_digest=hashlib.sha256(p.encode('utf-8')).hexdigest()) for p, s in manifest])
    return bundle


def owned_bundle(user, bundle_id, sealed=False, lock=False):
    _permission(user)
    qs = PanelUploadBundle.objects.select_for_update() if lock else PanelUploadBundle.objects
    try:
        bundle = qs.get(pk=_identifier(bundle_id), owner_id=user.pk)
    except PanelUploadBundle.DoesNotExist:
        raise PermissionError('文件包不存在或无权访问') from None
    if bundle.state not in ('draft', 'sealed'):
        raise ValueError('上传源文件已过期，请重新选择文件')
    if sealed and bundle.state != 'sealed':
        raise ValueError('文件包尚未封存')
    return bundle


def _lock_stream(stream, blocking=False):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))


def _open_part(path):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stream = os.fdopen(fd, 'w+b')
    try:
        _lock_stream(stream)
        return stream
    except Exception:
        stream.close()
        raise


def receive_file(user, bundle_id, file_id, uploaded_file):
    _permission(user)
    token, temp_path, stream = uuid.uuid4().hex, None, None
    source = None
    try:
        with transaction.atomic():
            bundle = owned_bundle(user, bundle_id, lock=True)
            if bundle.state != 'draft':
                raise ValueError('文件包已封存，不能修改')
            try:
                source = bundle.files.select_for_update().get(pk=_identifier(file_id))
            except PanelUploadFile.DoesNotExist:
                raise PermissionError('上传文件不存在或无权访问') from None
            if source.state != 'pending':
                raise ValueError('文件正在接收或已经完成，不能重复上传')
            _disk_space(source.size)
            temp_path = _directory(bundle.pk, create=True) / (source.pk + '.' + token + '.part')
            # The live OS lock is established before committing the claim.
            # Cleanup cannot unlink a slow upload even after the stale grace.
            stream = _open_part(temp_path)
            source.state, source.receive_token = 'receiving', token
            source.save(update_fields=('state', 'receive_token', 'updated_at'))
            bundle.save(update_fields=('updated_at',))
        digest, size = hashlib.sha256(), 0
        try:
            for chunk in uploaded_file.chunks():
                size += len(chunk)
                if size > source.size or size > limits()['file_bytes']:
                    raise ValueError('上传文件大小与清单不一致')
                digest.update(chunk)
                stream.write(chunk)
        except Exception:
            raise ValueError('上传接收未完成或大小不符，请核对状态后重试') from None
        if size != source.size:
            raise ValueError('上传文件大小与清单不一致')
        stream.flush()
        os.fsync(stream.fileno())
        with transaction.atomic():
            bundle = owned_bundle(user, bundle_id, lock=True)
            current = bundle.files.select_for_update().get(pk=source.pk)
            if bundle.state != 'draft' or current.state != 'receiving' or current.receive_token != token:
                raise ValueError('上传接收尝试已失效，请刷新状态')
            # Windows requires closing the open file before replacing it. The
            # bundle row lock protects the gap from cleanup and other receivers.
            stream.close()
            stream = None
            os.replace(temp_path, _blob(current))
            current.state, current.sha256, current.receive_token = 'complete', digest.hexdigest(), ''
            current.save(update_fields=('state', 'sha256', 'receive_token', 'updated_at'))
            bundle.save(update_fields=('updated_at',))
        return current
    except (PermissionError, ValueError):
        raise
    except Exception:
        # Never propagate a source exception, filename, content, or storage path
        # into middleware logs or JSON responses.
        raise ValueError('上传接收未完成，请核对状态后重试') from None
    finally:
        if stream is not None:
            stream.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if source is not None:
            PanelUploadFile.objects.filter(pk=source.pk, state='receiving', receive_token=token).update(
                state='pending', receive_token='', updated_at=timezone.now())


def seal_bundle(user, bundle_id):
    with transaction.atomic():
        bundle = owned_bundle(user, bundle_id, lock=True)
        if bundle.state == 'sealed':
            return bundle
        files = list(bundle.files.order_by('relative_path'))
        if len(files) != bundle.file_count or any(f.state != 'complete' or not f.sha256 for f in files):
            raise ValueError('请先完成全部文件上传后再封存')
        manifest = [[f.relative_path, f.size, f.sha256] for f in files]
        bundle.manifest_sha256 = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        bundle.state = 'sealed'
        bundle.save(update_fields=('state', 'manifest_sha256', 'updated_at'))
    return bundle


def blob_path(source):
    try:
        current = PanelUploadFile.objects.select_related('bundle').get(pk=source.pk, bundle_id=source.bundle_id)
    except PanelUploadFile.DoesNotExist:
        raise ValueError('上传源文件不可用') from None
    if current.bundle.state not in ('draft', 'sealed') or current.state != 'complete':
        raise ValueError('上传源文件不可用或已过期')
    path = _blob(current)
    if not path.is_file() or path.stat().st_size != current.size:
        raise ValueError('上传源文件缺失或大小变化，请重新选择文件')
    return path


def bundle_view(bundle):
    return dict(id=bundle.pk, state=bundle.state, total_bytes=bundle.total_bytes, file_count=bundle.file_count,
                manifest_sha256=bundle.manifest_sha256,
                files=[dict(id=f.pk, path=f.relative_path, size=f.size, state=f.state)
                       for f in bundle.files.order_by('relative_path')])


def _remove_blobs(bundle, stale_before=None):
    """Called under the bundle lock. Return False if a live part is present.

    Lock all parts before deleting anything. An unlocked old part belongs to a
    crashed process; a locked part remains protected regardless of its age.
    """
    directory = _directory(bundle.pk)
    if not directory.exists():
        return True
    held, paths = [], []
    try:
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file() or not (_BLOB.fullmatch(path.name) or _PART.fullmatch(path.name)):
                raise ValueError('上传私有存储内容无效')
            if _PART.fullmatch(path.name):
                if stale_before is not None and path.stat().st_mtime > stale_before.timestamp():
                    return False
                part = path.open('r+b')
                try:
                    _lock_stream(part)
                except OSError:
                    part.close()
                    return False
                held.append(part)
            paths.append(path)
        # All receivers use the same bundle lock during claim and finish, so
        # closing these handles is safe even on Windows before unlinking them.
        for part in held:
            part.close()
        held.clear()
        for path in paths:
            path.unlink(missing_ok=True)
        directory.rmdir()
        return True
    finally:
        for part in held:
            part.close()


def discard_bundle(user, bundle_id):
    _permission(user)
    with transaction.atomic():
        _quota_lock()
        bundle = owned_bundle(user, bundle_id, lock=True)
        if bundle.bindings.exists() or bundle.files.filter(state='receiving').exists():
            raise ValueError('文件包已绑定任务或正在接收，不能释放')
        if not _remove_blobs(bundle):
            raise ValueError('文件包正在接收，不能释放')
        bundle.state = 'expired'
        bundle.save(update_fields=('state', 'updated_at'))
    return bundle


def cleanup_expired():
    """Bounded, quiet local cleanup. Metadata and result rows are retained."""
    now = timezone.now()
    # A conservative terminal allowlist protects new/unknown task states too.
    candidates = PanelUploadBundle.objects.filter(updated_at__lt=now - _DAY).order_by('updated_at')
    expired = 0
    for bundle_id in candidates.values_list('pk', flat=True).iterator(chunk_size=100):
        with transaction.atomic():
            _quota_lock()
            bundle = PanelUploadBundle.objects.select_for_update().get(pk=bundle_id)
            if bundle.updated_at >= now - _DAY:
                continue
            bindings = list(bundle.bindings.select_related('target__task'))
            if bindings:
                cutoff = now - timedelta(days=7)
                if any(b.target.state not in _ENDED or b.target.task.state not in _ENDED or
                       b.target.updated_at >= cutoff or b.target.task.updated_at >= cutoff for b in bindings):
                    continue
            elif bundle.state not in ('draft', 'expired'):
                continue
            # A recently claimed receive is always protected, even if bundle
            # timestamps were imported from an older installation.
            if bundle.files.filter(state='receiving', updated_at__gte=now - _DAY).exists():
                continue
            if not _remove_blobs(bundle, stale_before=now - _DAY):
                continue
            bundle.files.filter(state='receiving').update(state='pending', receive_token='', updated_at=now)
            if bundle.state != 'expired':
                bundle.state = 'expired'
                bundle.save(update_fields=('state', 'updated_at'))
                expired += 1
    return {'expired': expired}
