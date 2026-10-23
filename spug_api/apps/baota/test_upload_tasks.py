"""Upload queue integration with an isolated in-memory panel filesystem."""
import hashlib
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings

from . import models, task_service as s, upload_store as store
from .client import PanelAPIError
from . import tests as legacy
from .worker import PanelWorker, recover_interrupted


class UploadPanel:
    def __init__(self):
        self.dirs = {'/', '/www', '/www/site', '/other'}
        self.files = {'/www/site/a.txt': b'old'}
        self.links = set()
        self.calls = []
        self.timeout = False
        self.bad_hash = False
        self.cancel = None

    def __call__(self, panel):
        self.panel = panel
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, path, action, params=None, **kwargs):
        assert not connection.in_atomic_block
        self.calls.append((path, action))
        params = params or {}
        if action == 'GetSystemTotal':
            return {'version': '13.1.0', 'system': 'Ubuntu 22.04 Linux'}
        if action == 'GetDirNew':
            root = params['path']
            if root not in self.dirs:
                return {'path': '/www', 'dir': [], 'files': []}
            rows = {'path': root, 'dir': [], 'files': []}
            for name in sorted(self.dirs | set(self.files) | self.links):
                if name == '/' or str(Path(name).parent).replace('\\', '/') != root:
                    continue
                entry = {'nm': name.rsplit('/', 1)[1], 'sn': name.rsplit('/', 1)[1],
                         'sz': len(self.files.get(name, b'')), 'mt': 100,
                         'lnk': '->/outside' if name in self.links else ''}
                rows['dir' if name in self.dirs else 'files'].append(entry)
            page, size = params.get('p', 1), params.get('showRow', 100)
            flat = [('dir', r) for r in rows['dir']] + [('files', r) for r in rows['files']]
            return {'path': root, 'dir': [r for k, r in flat[(page-1)*size:page*size] if k == 'dir'],
                    'files': [r for k, r in flat[(page-1)*size:page*size] if k == 'files']}
        if action == 'upload_files_exists':
            return [{'filename': name, 'exists': name in self.files or name in self.dirs,
                     'isfile': name in self.files, 'size': len(self.files.get(name, b'')), 'mtime': 100}
                    for name in params['files'].split('\n')]
        raise AssertionError('Unexpected panel action: ' + action)

    def upload_file(self, directory, filename, stream, **kwargs):
        assert not connection.in_atomic_block
        row = models.PanelUploadResult.objects.get(state='dispatching', target__panel=self.panel)
        assert row.intent and models.PanelLock.objects.filter(target=row.target).exists()
        self.calls.append(('UPLOAD', directory + '/' + filename))
        self.files[directory + '/' + filename] = stream.read()
        parts = directory.split('/')
        self.dirs.update('/'.join(parts[:n]) or '/' for n in range(1, len(parts) + 1))
        if self.cancel:
            self.cancel()
        if self.timeout:
            raise TimeoutError('secret transport response')
        return {'status': True}

    def file_sha256(self, filename, maximum, **kwargs):
        assert not connection.in_atomic_block
        self.calls.append(('HASH', filename))
        value = self.files[filename]
        return {'sha256': '0' * 64 if self.bad_hash else hashlib.sha256(value).hexdigest(), 'size': len(value)}


class UploadTaskTests(TransactionTestCase):
    setUp = legacy.BaotaAPITests.setUp
    make_user = legacy.BaotaAPITests.make_user
    authenticate = legacy.BaotaAPITests.authenticate
    request = legacy.BaotaAPITests.request
    group = legacy.BaotaAPITests.group
    panel = legacy.BaotaAPITests.panel

    def init_upload(self, paths=('a.txt', 'dist/b.txt')):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = override_settings(BAOTA_UPLOAD_ROOT=str(Path(self.tmp.name) / 'private'),
                                   BAOTA_UPLOAD_LIMITS={'reserve_bytes': 0})
        config.enable()
        self.addCleanup(config.disable)
        self.bundle = store.create_bundle(self.admin, [{'path': p, 'size': 3} for p in paths])
        for source in self.bundle.files.all():
            store.receive_file(self.admin, self.bundle.pk, source.pk, SimpleUploadedFile('x', b'new'))
        store.seal_bundle(self.admin, self.bundle.pk)
        self.record = self.panel(api_base_url='https://example.com', api_key='fixture')
        models.Panel.objects.filter(pk=self.record['id']).update(api_checked_at='checked', api_version='13.1.0')
        self.remote = UploadPanel()
        self.worker = PanelWorker(client_factory=self.remote)

    def prepare_upload(self, **changes):
        data = {'operation': 'file_upload', 'panel_ids': [self.record['id']],
                'params': {'bundle_id': self.bundle.pk, 'target_dir': '/www/site'}}
        data.update(changes)
        return s.prepare(self.admin, data)

    def prepared(self):
        task = self.prepare_upload()
        self.worker.run_once()
        task.refresh_from_db()
        self.assertEqual(task.state, 'prepared')
        return task

    def started(self):
        task = self.prepared()
        s.start(self.admin, task.pk, {'acknowledgements': ['upload_overwrite']})
        return task

    def test_common_and_partial_individual_directory_precedence_is_frozen(self):
        self.init_upload()
        second = self.panel(name='second', group_id=self.record['group_id'])
        models.Panel.objects.filter(pk=second['id']).update(api_checked_at='checked', api_version='13.1.0')
        task = self.prepare_upload(panel_ids=[self.record['id'], second['id']],
            params_by_panel={str(second['id']): {'target_dir': ' /other/ '}})
        self.assertEqual(task.targets.get(panel_id=self.record['id']).params['target_dir'], '/www/site')
        frozen = task.targets.get(panel_id=second['id']).params
        self.assertEqual((frozen['target_dir'], frozen['directory_source']), ('/other', 'individual'))
        self.assertEqual(models.PanelUploadBinding.objects.filter(bundle=self.bundle).count(), 2)

    def test_invalid_individual_path_cannot_fall_back_and_missing_paths_block(self):
        self.init_upload()
        for path in ('../bad', '/', '/a//b', 42):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.prepare_upload(params_by_panel={str(self.record['id']): {'target_dir': path}})
        with self.assertRaises(ValueError):
            self.prepare_upload(params={'bundle_id': self.bundle.pk, 'target_dir': ''})
        self.assertFalse(models.PanelTask.objects.exists())

    def test_bundle_must_be_sealed_owned_and_immutable_for_retry(self):
        self.init_upload()
        models.PanelUploadBundle.objects.filter(pk=self.bundle.pk).update(state='draft')
        with self.assertRaises(ValueError):
            self.prepare_upload()
        models.PanelUploadBundle.objects.filter(pk=self.bundle.pk).update(state='sealed')
        other = self.make_user('other', superuser=True)
        with self.assertRaises(PermissionError):
            s.prepare(other, {'operation': 'file_upload', 'panel_ids': [self.record['id']],
                             'params': {'bundle_id': self.bundle.pk, 'target_dir': '/www/site'}})
        task = self.prepare_upload()
        models.PanelUploadBundle.objects.filter(pk=self.bundle.pk).update(state='expired')
        with self.assertRaises(ValueError):
            s.retry(self.admin, task.pk)

    def test_preflight_stores_files_separately_and_requires_explicit_overwrite_ack(self):
        self.init_upload()
        task = self.prepared()
        target = task.targets.get()
        self.assertEqual(len(target.children), 1)
        self.assertEqual(target.upload_files.count(), 2)
        self.assertEqual(target.summary['upload']['target_dir'], '/www/site')
        self.assertEqual(target.summary['upload']['pending'], 2)
        self.assertEqual(s.required_acks([target]), ['upload_overwrite'])
        self.assertFalse(any(call[0] == 'UPLOAD' for call in self.remote.calls))
        with self.assertRaises(ValueError):
            s.start(self.admin, task.pk, {'acknowledgements': []})

    def test_symlink_and_file_directory_conflicts_are_rejected_before_writes(self):
        self.init_upload()
        self.remote.links.add('/www/site')
        task = self.prepared()
        target = task.targets.get()
        self.assertEqual(target.summary['upload']['failed'], 2)
        self.assertEqual(s.required_acks([target]), [])
        self.assertFalse(any(call[0] == 'UPLOAD' for call in self.remote.calls))

    def test_each_file_has_committed_intent_then_hash_verified_success(self):
        self.init_upload()
        task = self.started()
        for _ in range(4):
            self.worker.run_once()
        target = task.targets.get()
        self.assertEqual(target.state, 'completed')
        self.assertEqual(target.summary['upload']['success'], 2)
        self.assertEqual(models.PanelUploadResult.objects.filter(state='success').count(), 2)
        self.assertEqual(self.remote.files['/www/site/dist/b.txt'], b'new')
        self.assertFalse(models.PanelLock.objects.exists())

    def test_timeout_and_hash_mismatch_stay_uncertain_without_automatic_resend(self):
        self.init_upload()
        task = self.started()
        self.remote.timeout = True
        self.worker.run_once()
        self.assertFalse(self.worker.run_once())
        target = task.targets.get()
        self.assertEqual(target.state, 'uncertain')
        self.assertTrue(models.PanelLock.objects.filter(target=target).exists())
        self.assertEqual(sum(c[0] == 'UPLOAD' for c in self.remote.calls), 1)
        self.remote.timeout = False
        self.remote.bad_hash = True
        s.reconcile(self.admin, task.pk)
        self.worker.run_once()
        self.assertEqual(task.targets.get().state, 'uncertain')
        self.assertEqual(sum(c[0] == 'UPLOAD' for c in self.remote.calls), 1)
        self.remote.bad_hash = False
        s.reconcile(self.admin, task.pk)
        self.worker.run_once()
        self.assertEqual(models.PanelUploadResult.objects.filter(state='success').count(), 1)

    def test_cancellation_during_upload_finishes_current_hash_and_cancels_unsent_files(self):
        self.init_upload()
        task = self.started()
        self.remote.cancel = lambda: s.cancel(self.admin, task.pk)
        self.worker.run_once()
        self.assertEqual(models.PanelUploadResult.objects.filter(state='success').count(), 1)
        self.assertEqual(models.PanelUploadResult.objects.filter(state='cancelled').count(), 1)
        self.assertFalse(models.PanelLock.objects.exists())

    def test_restart_only_marks_file_with_persisted_intent_uncertain(self):
        self.init_upload()
        task = self.started()
        target = task.targets.get()
        row = target.upload_files.first()
        row.state, row.intent = 'dispatching', {'at': s.now()}
        row.save()
        target.state = 'dispatching'
        target.save()
        models.PanelLock.objects.create(panel_id=target.panel_id, target=target)
        recover_interrupted()
        self.assertEqual(target.upload_files.filter(state='uncertain').count(), 1)
        self.assertEqual(target.upload_files.filter(state='ready').count(), 1)
        self.assertFalse(self.worker.run_once())

    def test_results_are_owner_scoped_paginated_and_manual_resolution_is_per_file(self):
        self.init_upload()
        task = self.started()
        self.remote.timeout = True
        self.worker.run_once()
        target = task.targets.get()
        result = self.request('get', f'/baota/targets/{target.pk}/uploads/', {'page_size': 1})
        self.assertEqual((result['total'], len(result['items'])), (2, 1))
        row = target.upload_files.get(state='uncertain')
        s.resolve(self.admin, task.pk, {'target_id': target.pk, 'child_key': 'file:' + row.source_id,
                 'outcome': 'failed', 'reason': '手动核对', 'evidence': '内容不一致'})
        row.refresh_from_db()
        self.assertEqual(row.state, 'failed')
        outsider = self.make_user('outsider')
        self.authenticate(outsider)
        self.request('get', f'/baota/targets/{target.pk}/uploads/', success=False)

    def test_remote_file_changed_after_preflight_is_not_overwritten(self):
        self.init_upload(paths=('a.txt',))
        task = self.started()
        self.remote.files['/www/site/a.txt'] = b'changed-content'
        self.worker.run_once()
        self.assertEqual(models.PanelUploadResult.objects.get().state, 'failed')
        self.assertFalse(any(c[0] == 'UPLOAD' for c in self.remote.calls))

    def test_source_checksum_change_prevents_remote_write(self):
        self.init_upload(paths=('a.txt',))
        task = self.started()
        source = self.bundle.files.get()
        store.blob_path(source).write_bytes(b'bad')
        self.worker.run_once()
        self.assertEqual(models.PanelUploadResult.objects.get().state, 'failed')
        self.assertFalse(any(c[0] == 'UPLOAD' for c in self.remote.calls))

    def test_missing_destination_directories_are_created_by_the_single_upload_call(self):
        self.init_upload(paths=('dist/assets/a.txt',))
        task = self.prepare_upload(params={'bundle_id': self.bundle.pk, 'target_dir': '/new/website'})
        self.worker.run_once()
        s.start(self.admin, task.pk, {'acknowledgements': ['upload_overwrite']})
        self.worker.run_once()
        self.assertEqual(self.remote.files['/new/website/dist/assets/a.txt'], b'new')
        self.assertEqual(models.PanelUploadResult.objects.get().state, 'success')
        self.assertEqual(sum(c[0] == 'UPLOAD' for c in self.remote.calls), 1)

    def test_symlink_inserted_after_preflight_blocks_write_and_content_readback(self):
        self.init_upload(paths=('a.txt',))
        task = self.started()
        self.remote.links.add('/www/site')
        self.worker.run_once()
        self.assertEqual(models.PanelUploadResult.objects.get().state, 'failed')
        self.assertFalse(any(c[0] in ('UPLOAD', 'HASH') for c in self.remote.calls))

    def test_empty_file_is_uploaded_and_hash_verified(self):
        self.init_upload(paths=('a.txt',))
        empty = store.create_bundle(self.admin, [{'path': 'empty.txt', 'size': 0}])
        source = empty.files.get()
        store.receive_file(self.admin, empty.pk, source.pk, SimpleUploadedFile('empty', b''))
        store.seal_bundle(self.admin, empty.pk)
        task = self.prepare_upload(params={'bundle_id': empty.pk, 'target_dir': '/www/site'})
        self.worker.run_once()
        s.start(self.admin, task.pk, {'acknowledgements': ['upload_overwrite']})
        self.worker.run_once()
        self.assertEqual(self.remote.files['/www/site/empty.txt'], b'')
        self.assertEqual(models.PanelUploadResult.objects.get().state, 'success')

    def test_remote_preflight_cache_has_cumulative_entry_and_directory_bounds(self):
        from .operations.uploads import RemotePaths
        remote = UploadPanel()
        remote.dirs.update('/d' + str(i) for i in range(6))
        remote.files.update({f'/d{i}/f{j}': b'x' for i in range(6) for j in range(4)})
        paths = RemotePaths(remote)
        paths.MAX_CACHE_ENTRIES, paths.MAX_CACHE_DIRS = 8, 2
        for i in range(6):
            paths.listing('/d' + str(i))
            self.assertLessEqual(sum(len(value) for value in paths.cache.values()), 8)
            self.assertLessEqual(len(paths.cache), 2)
        self.assertEqual(set(paths.listing('/d0')), {'f0', 'f1', 'f2', 'f3'})

    def test_remote_directory_rejects_oversize_entry_names(self):
        from .operations.uploads import RemotePaths
        remote = UploadPanel()
        remote.files['/' + 'x' * 256] = b''
        with self.assertRaises(PanelAPIError):
            RemotePaths(remote).listing('/')
