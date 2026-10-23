"""Private upload staging tests. No panel or other remote service is contacted."""
import hashlib
import importlib
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.db import connection
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.urls import Resolver404, resolve
from django.utils import timezone

from . import models
from . import tests as legacy


class UploadStoreTests(TransactionTestCase):
    make_user = legacy.BaotaAPITests.make_user

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = override_settings(BAOTA_UPLOAD_ROOT=str(Path(self.tmp.name) / 'private'),
                                       BAOTA_UPLOAD_LIMITS={'reserve_bytes': 0})
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.user = self.make_user('upload-owner', superuser=True)

    def store(self):
        self.assertIsNotNone(importlib.util.find_spec('apps.baota.upload_store'),
                             'private upload storage API is missing')
        return importlib.import_module('apps.baota.upload_store')

    def bundle(self, files=None):
        return self.store().create_bundle(self.user, files or [{'path': 'dist/a.txt', 'size': 3}])

    def receive(self, bundle, content=b'abc'):
        source = bundle.files.first()
        return self.store().receive_file(self.user, bundle.pk, source.pk,
                                         SimpleUploadedFile('ignored-secret-name.txt', content))

    def target(self, state='completed'):
        group = models.PanelGroup.objects.create(name='fixture')
        panel = models.Panel.objects.create(name='fixture', url='https://example.com', username='fixture',
            password_ciphertext='never-return-password', api_key_ciphertext='never-return-api-key',
            group=group, created_by=self.user)
        task = models.PanelTask.objects.create(owner=self.user, operation='file_upload', state=state)
        return models.PanelTaskTarget.objects.create(task=task, panel=panel, state=state)

    def bind(self, bundle, state='completed'):
        target = self.target(state)
        models.PanelUploadBinding.objects.create(target=target, bundle=bundle)
        return target

    def age(self, bundle, days=2):
        when = timezone.now() - timedelta(days=days)
        models.PanelUploadBundle.objects.filter(pk=bundle.pk).update(updated_at=when, created_at=when)
        return when

    def test_public_api_and_models_exist(self):
        store = self.store()
        for name in ('limits', 'absolute_directory', 'effective_directory', 'relative_path', 'create_bundle',
                     'owned_bundle', 'receive_file', 'seal_bundle', 'discard_bundle', 'blob_path',
                     'bundle_view', 'cleanup_expired'):
            self.assertTrue(callable(getattr(store, name, None)), name)
        for name in ('PanelUploadBundle', 'PanelUploadFile', 'PanelUploadBinding',
                     'PanelUploadResult', 'PanelUploadQuota'):
            self.assertTrue(hasattr(models, name), name)

    def test_paginated_upload_results_route_is_reserved(self):
        try:
            match = resolve('/baota/targets/123/uploads/')
        except Resolver404:
            self.fail('paginated upload results endpoint is missing')
        self.assertEqual(match.kwargs, {'target_id': 123})

    def test_path_validation_and_individual_directory_priority(self):
        store = self.store()
        self.assertEqual(store.absolute_directory('/www/site/'), '/www/site')
        self.assertEqual(store.effective_directory(' /common ', ' /site-a '), '/site-a')
        self.assertEqual(store.effective_directory(' /common ', '   '), '/common')
        self.assertEqual(store.effective_directory('', '/individual'), '/individual')
        for common, individual in [('/common', '../bad'), ('', None), ('/ok', 42)]:
            with self.assertRaises(ValueError):
                store.effective_directory(common, individual)
        for value in ['', '/', 'relative', '/a/../b', '/a/./b', '/a//b', '/~a', '/a\\b', '/a\nsecret', '/a\x00b', '/a&b', '/a|b']:
            with self.subTest(directory=value), self.assertRaises(ValueError):
                store.absolute_directory(value)
        for value in ['', '/absolute', '../a', 'a/../b', './a', 'a//b', 'a/', 'a\\b', 'a\nb',
                      'a\x00b', 'C:/a', '~a', 'a&b', 'a|b', 'é' * 128]:
            with self.subTest(path=value), self.assertRaises(ValueError):
                store.relative_path(value)
        self.assertEqual(store.relative_path('dist/子目录/a.txt'), 'dist/子目录/a.txt')

    def test_manifest_validation_prefix_collisions_and_limits(self):
        store = self.store()
        for files in [[], [{'path': 'a', 'size': -1}], [{'path': 'a', 'size': True}],
                      [{'path': 'a', 'size': 1}, {'path': 'a', 'size': 2}],
                      [{'path': 'a/b', 'size': 1}, {'path': 'a', 'size': 2}],
                      [{'path': 'a', 'size': 1}, {'path': 'a/b', 'size': 2}],
                      [{'path': '../a', 'size': 1}], [{'path': 'a', 'size': 20 * 1024**2 + 1}]]:
            with self.subTest(files=files), self.assertRaises(ValueError):
                store.create_bundle(self.user, files)
        with override_settings(BAOTA_UPLOAD_LIMITS={'files': 1, 'bundle_bytes': 3, 'reserve_bytes': 0}):
            for files in [[{'path': 'a', 'size': 1}, {'path': 'b', 'size': 1}], [{'path': 'a', 'size': 4}]]:
                with self.assertRaises(ValueError):
                    store.create_bundle(self.user, files)
        self.assertFalse(models.PanelUploadBundle.objects.exists())

    def test_file_limit_can_be_lowered_but_cannot_exceed_transport_hard_cap(self):
        store = self.store()
        self.assertEqual(store.limits()['file_bytes'], 20 * 1024**2)
        with override_settings(BAOTA_UPLOAD_LIMITS={'file_bytes': 20 * 1024**2 + 1}):
            with self.assertRaisesRegex(ValueError, '上传限制配置无效'):
                store.limits()
            with self.assertRaises(ValueError):
                self.bundle()
        with override_settings(BAOTA_UPLOAD_LIMITS={'file_bytes': 2, 'reserve_bytes': 0}):
            self.assertEqual(store.limits()['file_bytes'], 2)
            with self.assertRaises(ValueError):
                self.bundle()
            bundle = self.bundle([{'path': 'small.txt', 'size': 2}])
            source = store.receive_file(self.user, bundle.pk, bundle.files.get().pk,
                                       SimpleUploadedFile('small.txt', b'ab'))
            self.assertEqual(store.blob_path(source).read_bytes(), b'ab')

    def test_owner_and_create_permission_required_even_for_manager(self):
        store = self.store()
        bundle = self.bundle()
        other = self.make_user('other', superuser=True)
        denied = self.make_user('denied')
        manager = self.make_user('manager')
        manager.has_perms = lambda codes: codes == ['exec.panel.manage']
        for user in (other, denied, manager):
            with self.assertRaises(PermissionError):
                store.owned_bundle(user, bundle.pk)
            with self.assertRaises(PermissionError):
                store.receive_file(user, bundle.pk, bundle.files.first().pk, SimpleUploadedFile('a', b'abc'))
        with self.assertRaises(PermissionError):
            store.create_bundle(denied, [{'path': 'a', 'size': 1}])
        with self.assertRaises(ValueError):
            store.owned_bundle(self.user, bundle.pk, sealed=True)

    def test_global_user_and_disk_space_quotas_reserve_manifest_bytes(self):
        store = self.store()
        self.bundle()
        with override_settings(BAOTA_UPLOAD_LIMITS={'global_bytes': 5, 'reserve_bytes': 0}):
            with self.assertRaises(ValueError):
                self.bundle()
        with override_settings(BAOTA_UPLOAD_LIMITS={'user_bytes': 5, 'reserve_bytes': 0}):
            with self.assertRaises(ValueError):
                self.bundle()
        with patch('apps.baota.upload_store.shutil.disk_usage', return_value=(100, 99, 1)):
            with self.assertRaises(ValueError):
                self.bundle()
        store.discard_bundle(self.user, models.PanelUploadBundle.objects.first().pk)
        with override_settings(BAOTA_UPLOAD_LIMITS={'global_bytes': 3, 'reserve_bytes': 0}):
            self.bundle()

    def test_stream_real_bytes_private_random_paths_and_sealed_manifest(self):
        store = self.store()
        bundle = self.bundle([{'path': 'dist/z.txt', 'size': 3}, {'path': 'dist/a.txt', 'size': 0}])
        with self.assertRaises(ValueError):
            store.seal_bundle(self.user, bundle.pk)
        for source in bundle.files.all():
            content = b'abc' if source.size else b''
            source = store.receive_file(self.user, bundle.pk, source.pk, SimpleUploadedFile('secret-name', content))
            path = store.blob_path(source)
            self.assertEqual(Path(path).read_bytes(), content)
            self.assertNotIn('dist', str(path))
            self.assertNotIn('secret-name', str(path))
            self.assertEqual(source.sha256, hashlib.sha256(content).hexdigest())
            if os.name != 'nt':
                self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o700)
        bundle = store.seal_bundle(self.user, bundle.pk)
        self.assertEqual(bundle.state, 'sealed')
        self.assertEqual(len(bundle.manifest_sha256), 64)
        self.assertEqual(store.seal_bundle(self.user, bundle.pk).manifest_sha256, bundle.manifest_sha256)
        with self.assertRaises(ValueError):
            self.receive(bundle)
        view = store.bundle_view(bundle)
        self.assertEqual(set(view), {'id', 'state', 'total_bytes', 'file_count', 'manifest_sha256', 'files'})
        self.assertEqual(set(view['files'][0]), {'id', 'path', 'size', 'state'})
        self.assertNotIn('abc', json.dumps(view))
        self.assertNotIn('sha256', json.dumps(view['files']))

    def test_failed_stream_retries_safely_and_hides_raw_exception(self):
        store = self.store()
        bundle = self.bundle()
        class BrokenFile:
            def chunks(inner):
                self.assertFalse(connection.in_atomic_block)
                yield b'a'
                raise RuntimeError('access_token=DO-NOT-LOG secret source bytes')
        with self.assertRaises(ValueError) as error:
            store.receive_file(self.user, bundle.pk, bundle.files.first().pk, BrokenFile())
        self.assertNotIn('DO-NOT-LOG', str(error.exception))
        self.assertEqual(bundle.files.get().state, 'pending')
        self.assertEqual(bundle.files.get().receive_token, '')
        self.assertEqual(list(Path(self.tmp.name).rglob('*.part')), [])
        self.receive(bundle)
        with self.assertRaises(ValueError):
            self.receive(bundle)

    def test_size_mismatch_and_oversize_cannot_finish(self):
        store = self.store()
        bundle = self.bundle()
        for content in (b'ab', b'abcd'):
            with self.assertRaises(ValueError):
                self.receive(bundle, content)
            self.assertEqual(bundle.files.get().state, 'pending')
        source = self.receive(bundle)
        self.assertEqual(store.blob_path(source).read_bytes(), b'abc')

    def test_receiving_token_prevents_parallel_replacement_and_stale_completion(self):
        store = self.store()
        bundle = self.bundle()
        source = bundle.files.get()
        class Interleaved:
            def chunks(inner):
                self.assertFalse(connection.in_atomic_block)
                with self.assertRaises(ValueError):
                    self.receive(bundle)
                with self.assertRaises(ValueError):
                    store.seal_bundle(self.user, bundle.pk)
                with self.assertRaises(ValueError):
                    store.discard_bundle(self.user, bundle.pk)
                models.PanelUploadFile.objects.filter(pk=source.pk).update(receive_token='f' * 32)
                yield b'abc'
        with self.assertRaises(ValueError):
            store.receive_file(self.user, bundle.pk, source.pk, Interleaved())
        source.refresh_from_db()
        self.assertEqual(source.receive_token, 'f' * 32)
        self.assertEqual(source.state, 'receiving')
        self.assertEqual(list(Path(self.tmp.name).rglob('*.blob')), [])

    def test_discard_keeps_metadata_and_cannot_remove_bound_bundle(self):
        store = self.store()
        bundle = self.bundle()
        source = self.receive(bundle)
        path = store.blob_path(source)
        store.discard_bundle(self.user, bundle.pk)
        bundle.refresh_from_db()
        self.assertEqual(bundle.state, 'expired')
        self.assertTrue(bundle.files.filter(pk=source.pk, sha256=source.sha256).exists())
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            store.blob_path(source)
        other = self.bundle()
        self.bind(other)
        with self.assertRaises(ValueError):
            store.discard_bundle(self.user, other.pk)

    def test_cleanup_preserves_live_bindings_and_metadata_after_expiry(self):
        store = self.store()
        draft = self.bundle()
        source = self.receive(draft)
        draft_path = store.blob_path(source)
        self.age(draft)
        store.cleanup_expired()
        draft.refresh_from_db()
        self.assertEqual(draft.state, 'expired')
        self.assertFalse(draft_path.exists())
        for state in ('prepared', 'queued', 'running', 'uncertain', 'preflight_queued', 'dispatching'):
            bundle = self.bundle()
            source = self.receive(bundle)
            store.seal_bundle(self.user, bundle.pk)
            target = self.bind(bundle, state)
            old = self.age(bundle, 9)
            models.PanelTask.objects.filter(pk=target.task_id).update(updated_at=old)
            models.PanelTaskTarget.objects.filter(pk=target.pk).update(updated_at=old)
            store.cleanup_expired()
            bundle.refresh_from_db()
            self.assertEqual(bundle.state, 'sealed')
            self.assertTrue(store.blob_path(source).exists())
        ended = self.bundle()
        source = self.receive(ended)
        self.store().seal_bundle(self.user, ended.pk)
        target = self.bind(ended)
        old = self.age(ended, 8)
        models.PanelTask.objects.filter(pk=target.task_id).update(updated_at=old)
        models.PanelTaskTarget.objects.filter(pk=target.pk).update(updated_at=old)
        result = models.PanelUploadResult.objects.create(target=target, source=source, state='success')
        store.cleanup_expired()
        ended.refresh_from_db()
        self.assertEqual(ended.state, 'expired')
        self.assertTrue(models.PanelUploadResult.objects.filter(pk=result.pk).exists())
        self.assertTrue(models.PanelUploadFile.objects.filter(pk=source.pk).exists())

    def test_cleanup_recovers_crashed_receives_but_keeps_live_temp_file(self):
        store = self.store()
        bundle = self.bundle()
        source = bundle.files.get()
        old = self.age(bundle)
        models.PanelUploadFile.objects.filter(pk=source.pk).update(state='receiving', receive_token='c' * 32, updated_at=old)
        store.cleanup_expired()
        bundle.refresh_from_db()
        self.assertEqual(bundle.state, 'expired')
        live = self.bundle()
        class SlowFile:
            def chunks(inner):
                self.age(live)
                models.PanelUploadFile.objects.filter(bundle=live).update(updated_at=timezone.now() - timedelta(days=2))
                for path in Path(self.tmp.name).rglob('*.part'):
                    stamp = (timezone.now() - timedelta(days=2)).timestamp()
                    os.utime(path, (stamp, stamp))
                store.cleanup_expired()
                live.refresh_from_db()
                self.assertEqual(live.state, 'draft')
                self.assertTrue(list(Path(self.tmp.name).rglob('*.part')))
                yield b'abc'
        source = store.receive_file(self.user, live.pk, live.files.get().pk, SlowFile())
        self.assertEqual(store.blob_path(source).read_bytes(), b'abc')

    def test_http_create_receive_seal_and_errors_are_safe(self):
        self.store()
        self.client.defaults['HTTP_X_TOKEN'] = self.user.access_token
        response = self.client.post('/baota/uploads/', json.dumps({'files': [{'path': 'dist/a', 'size': 3}]}),
                                    content_type='application/json').json()
        self.assertFalse(response['error'], response)
        bundle = response['data']
        url = '/baota/uploads/%s/files/%s/' % (bundle['id'], bundle['files'][0]['id'])
        with patch('apps.baota.upload_views.receive_file', wraps=self.store().receive_file) as receive:
            response = self.client.post(url, {'file': SimpleUploadedFile('a', b'abc')}).json()
            self.assertFalse(response['error'], response)
            self.assertIsInstance(receive.call_args.args[3], TemporaryUploadedFile)
        response = self.client.post('/baota/uploads/%s/seal/' % bundle['id'], '{}', content_type='application/json').json()
        self.assertFalse(response['error'], response)
        self.assertEqual(response['data']['state'], 'sealed')
        self.assertEqual(self.client.get('/baota/uploads/').status_code, 405)

    def test_http_permissions_and_ownership_checked_before_parsing_body(self):
        self.store()
        views = importlib.import_module('apps.baota.upload_views')
        denied = self.make_user('no-upload-perms')
        factory = RequestFactory()
        for cls, kwargs in [(views.UploadBundlesView, {}), (views.UploadFileView, {'bundle_id': 'a'*32, 'file_id': 'b'*32}),
                            (views.UploadSealView, {'bundle_id': 'a'*32}), (views.UploadDiscardView, {'bundle_id': 'a'*32})]:
            request = factory.post('/baota/uploads/', b'not-json', content_type='application/json')
            request.user = denied
            with patch.object(type(request), 'body', new_callable=property, fget=lambda req: self.fail('body parsed before authorization')):
                response = cls.as_view()(request, **kwargs)
                self.assertTrue(json.loads(response.content)['error'])
        bundle = self.bundle()
        request = factory.post('/baota/uploads/', b'bad multipart', content_type='multipart/form-data; boundary=x')
        request.user = self.make_user('other-owner', superuser=True)
        response = views.UploadFileView.as_view()(request, bundle_id=bundle.pk, file_id=bundle.files.get().pk)
        self.assertTrue(json.loads(response.content)['error'])
        self.assertFalse(hasattr(request, '_files'))
        request = factory.post('/baota/uploads/', b'bad multipart', content_type='multipart/form-data; boundary=x')
        request.user = self.user
        request.META['CONTENT_LENGTH'] = str(21 * 1024**2 + 1)
        response = views.UploadFileView.as_view()(request, bundle_id=bundle.pk, file_id=bundle.files.get().pk)
        self.assertTrue(json.loads(response.content)['error'])
        self.assertFalse(hasattr(request, '_files'))

    def test_unicode_controls_and_html_alias_characters_are_rejected(self):
        store = self.store()
        for value in ['a\x85b', 'a\x9fb', 'a&#47;b', 'a|b']:
            with self.subTest(path=value):
                with self.assertRaises(ValueError):
                    store.relative_path(value)
                with self.assertRaises(ValueError):
                    store.absolute_directory('/' + value)

    def test_paths_have_1024_utf8_byte_total_limit_and_sanitize_invalid_unicode(self):
        store = self.store()
        oversized = '/'.join(['汉' * 80] * 5)
        self.assertLess(len(oversized), 1024)
        self.assertGreater(len(oversized.encode('utf-8')), 1024)
        for value in (oversized, 'folder/invalid\ud800'):
            with self.subTest(path=repr(value)):
                with self.assertRaises(ValueError) as error:
                    store.relative_path(value)
                self.assertEqual(str(error.exception), '文件路径无效')
                with self.assertRaises(ValueError) as error:
                    store.absolute_directory('/' + value)
                self.assertEqual(str(error.exception), '文件路径无效')
        # Exactly 1024 bytes is allowed for the entire absolute target path.
        valid = '/'.join(['a' * 255] * 4)
        self.assertEqual(len(valid.encode('utf-8')), 1023)
        self.assertEqual(store.relative_path(valid), valid)
        self.assertEqual(store.absolute_directory('/' + valid), '/' + valid)

    def test_stream_value_error_is_sanitized_and_final_manifest_is_order_independent(self):
        store = self.store()
        bundle = self.bundle()
        class SecretError:
            def chunks(inner):
                raise ValueError('secret source contents access_token=DO-NOT-RETURN')
        with self.assertRaises(ValueError) as error:
            store.receive_file(self.user, bundle.pk, bundle.files.get().pk, SecretError())
        self.assertNotIn('DO-NOT-RETURN', str(error.exception))
        digests = []
        for files in [[{'path': 'z/a', 'size': 0}, {'path': 'z/b', 'size': 1}],
                      [{'path': 'z/b', 'size': 1}, {'path': 'z/a', 'size': 0}]]:
            current = self.bundle(files)
            for source in current.files.all():
                store.receive_file(self.user, current.pk, source.pk, SimpleUploadedFile('irrelevant', b'x' * source.size))
            digests.append(store.seal_bundle(self.user, current.pk).manifest_sha256)
        self.assertEqual(*digests)

    def test_cleanup_retention_uses_all_task_bindings_and_latest_completion(self):
        store = self.store()
        bundle = self.bundle()
        source = self.receive(bundle)
        store.seal_bundle(self.user, bundle.pk)
        ended = self.bind(bundle)
        old = self.age(bundle, 9)
        models.PanelTask.objects.filter(pk=ended.task_id).update(updated_at=old)
        models.PanelTaskTarget.objects.filter(pk=ended.pk).update(updated_at=old)
        live = self.bind(bundle, 'prepared')
        store.cleanup_expired()
        self.assertTrue(store.blob_path(source).exists())
        live.state = 'completed'
        live.save()
        live.task.state = 'completed'
        live.task.save()
        store.cleanup_expired()
        self.assertTrue(store.blob_path(source).exists())

    def test_cleanup_expires_unbound_sealed_bundle_after_24_hours_and_releases_quota(self):
        store = self.store()
        bundle = self.bundle()
        source = self.receive(bundle)
        bundle = store.seal_bundle(self.user, bundle.pk)
        manifest = bundle.manifest_sha256
        path = store.blob_path(source)
        self.age(bundle, 23 / 24)
        with override_settings(BAOTA_UPLOAD_LIMITS={'global_bytes': 3, 'reserve_bytes': 0}):
            store.cleanup_expired()
            bundle.refresh_from_db()
            self.assertEqual(bundle.state, 'sealed')
            self.assertTrue(path.exists())
            with self.assertRaises(ValueError):
                self.bundle()
            self.age(bundle, 25 / 24)
            store.cleanup_expired()
            bundle.refresh_from_db()
            self.assertEqual(bundle.state, 'expired')
            self.assertFalse(path.exists())
            self.assertEqual(bundle.manifest_sha256, manifest)
            self.assertEqual(bundle.files.get().sha256, source.sha256)
            self.assertEqual(self.bundle().state, 'draft')

    def test_blob_lookup_rejects_symlink_and_missing_content(self):
        store = self.store()
        bundle = self.bundle()
        source = self.receive(bundle)
        path = store.blob_path(source)
        path.unlink()
        with self.assertRaises(ValueError):
            store.blob_path(source)
        outside = Path(self.tmp.name) / 'outside'
        outside.write_bytes(b'abc')
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('symlink creation requires Windows developer mode or administrative access')
        with self.assertRaises(ValueError):
            store.blob_path(source)
