"""Host/SSH/file authorization, isolated DB and mocked network boundaries."""
import io
import json
import time
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

from django.core.cache import cache
from django.test import TestCase, override_settings
from openpyxl import Workbook

from apps.account import access
from apps.account.models import AccountResourcePolicy, ResourceGrant, ResourceGroupOwner, Role, User
from apps.account.utils import has_host_perm
from apps.exec.models import ExecHistory, ExecTemplate, Transfer
from apps.host.models import Group, Host


@override_settings(ROOT_URLCONF='apps.account.host_resource_test_urls')
class HostResourceTests(TestCase):
    def setUp(self):
        cache.clear()
        # Inline WebSocket/worker fixtures share TestCase's outer transaction;
        # production cleanup runs on the actual long-lived worker connection.
        for target in ('consumer.utils.close_old_connections', 'apps.exec.transfer.close_old_connections'):
            cleanup = patch(target)
            cleanup.start()
            self.addCleanup(cleanup.stop)
        self.admin = self.user('admin', True)
        self.a, self.b = self.user('a'), self.user('b')
        self.ga, self.gb = Group.objects.create(name='A'), Group.objects.create(name='B')
        ResourceGroupOwner.objects.create(kind='host', group_id=self.ga.pk, owner=self.a)
        ResourceGroupOwner.objects.create(kind='host', group_id=self.gb.pk, owner=self.b)
        self.ha = self.host('A', self.a, self.ga)
        self.hb = self.host('B', self.b, self.gb)
        self.perms(self.a, 'host.host.view', 'host.host.add', 'host.host.edit', 'host.host.del',
                   'host.host.verify', 'host.host.import', 'host.host.export', 'host.group.view',
                   'host.group.add', 'host.group.edit', 'host.group.del', 'host.group.move',
                   'host.console.view', 'host.console.list', 'host.console.upload', 'host.console.del',
                   'exec.task.do', 'exec.template.view', 'exec.template.add', 'exec.template.edit',
                   'exec.template.del', 'exec.transfer.do')
        AccountResourcePolicy.objects.create(user=self.a, server_limit=10)

    def user(self, name, admin=False):
        return User.objects.create(username=name, is_supper=admin, access_token=name.ljust(32, 'x'),
                                   token_expired=int(time.time()) + 3600, last_ip='')

    def host(self, name, user, group):
        h = Host.objects.create(name=name, hostname='192.0.2.%s' % user.pk,
                                port=22, username='root', pkey='secret-' + name, created_by=user)
        h.groups.add(group)
        return h

    def perms(self, user, *codes):
        perms = {}
        for code in codes:
            module, page, action = code.split('.')
            perms.setdefault(module, {}).setdefault(page, []).append(action)
        role = Role.objects.create(name='role-%s' % user.pk, page_perms=perms,
                                   group_perms=[self.ga.pk, self.gb.pk])
        user.roles.set([role])
        cache.clear()

    def call(self, path, method='get', data=None, user=None):
        user = user or self.a
        kwargs = {'HTTP_X_TOKEN': user.access_token}
        if method in ('post', 'patch', 'put'):
            kwargs.update(data=json.dumps(data or {}), content_type='application/json')
        elif data:
            if method == 'delete':
                path += '?' + urlencode(data)
            else:
                kwargs['data'] = data
        return getattr(self.client, method)(path, **kwargs)

    def assert_denied(self, response):
        self.assertTrue(response.json()['error'], response.content)

    def form(self, **kwargs):
        return dict(name='new', hostname='192.0.2.10', port=22, username='root',
                    group_ids=[self.ga.pk], pkey='explicit-private', **kwargs)

    def history(self, user=None, hosts=None):
        return ExecHistory.objects.create(user=user or self.a, digest='t' * 32, interpreter='sh',
            command='true', host_ids=json.dumps(hosts or [self.ha.pk]))

    def test_legacy_group_roles_do_not_grant_other_owners_hosts(self):
        self.assertFalse(has_host_perm(self.a, self.hb.pk))
        self.assertTrue(has_host_perm(self.a, self.ha.pk))

    def test_host_list_limits_to_ownership_and_explicit_grants(self):
        self.assertEqual([self.ha.pk], [x['id'] for x in self.call('/host/').json()['data']])

    def test_group_tree_excludes_foreign_hosts_even_in_shared_group(self):
        self.hb.groups.add(self.ga)
        data = self.call('/host/group/?with_hosts=1').json()['data']
        self.assertNotIn('secret-', str(data))
        self.assertNotIn('B', [c['title'] for g in data['treeData'] for c in g['children']])

    def test_group_tree_distinguishes_owned_and_readable_only_groups(self):
        rows = self.call('/host/group/').json()['data']['treeData']
        self.assertTrue(next(g for g in rows if g['key'] == self.ga.pk)['can_manage'])
        self.assertFalse(next(g for g in rows if g['key'] == self.gb.pk)['can_manage'])

    @patch('apps.host.views._do_host_verify', return_value=True)
    def test_edit_other_host_is_rejected_before_saved_key_or_network(self, verify):
        self.assert_denied(self.call('/host/', 'post', self.form(id=self.hb.pk, keep_pkey=True)))
        verify.assert_not_called()
        self.hb.refresh_from_db()
        self.assertEqual(self.hb.name, 'B')

    @patch('apps.host.views._do_host_verify', return_value=True)
    def test_add_permission_does_not_authorize_edit(self, verify):
        self.perms(self.a, 'host.host.add')
        self.assert_denied(self.call('/host/', 'post', self.form(id=self.ha.pk)))
        verify.assert_not_called()

    @patch('apps.host.views._do_host_verify', return_value=True)
    def test_read_grant_cannot_edit(self, verify):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        self.assert_denied(self.call('/host/', 'post', self.form(id=self.hb.pk)))
        verify.assert_not_called()

    @patch('apps.host.views._do_host_verify', return_value=True)
    def test_add_rejects_foreign_group_before_network(self, verify):
        data = self.form()
        data['group_ids'] = [self.gb.pk]
        self.assert_denied(self.call('/host/', 'post', data))
        verify.assert_not_called()

    @patch('apps.host.views._do_host_verify', return_value=True)
    def test_zero_quota_rolls_back_new_host(self, verify):
        AccountResourcePolicy.objects.filter(user=self.a).update(server_limit=0)
        self.assert_denied(self.call('/host/', 'post', self.form()))
        self.assertFalse(Host.objects.filter(name='new').exists())

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key')
    def test_new_ordinary_host_does_not_borrow_global_key(self, key, ssh):
        data = self.form()
        data.pop('pkey')
        self.assert_denied(self.call('/host/', 'post', data))
        key.assert_not_called()
        ssh.assert_not_called()

    def test_host_delete_rejects_foreign_id(self):
        self.assert_denied(self.call('/host/', 'delete', {'id': self.hb.pk}))
        self.assertTrue(Host.objects.filter(pk=self.hb.pk).exists())

    def test_owner_can_delete_unreferenced_host(self):
        result = self.call('/host/', 'delete', {'id': self.ha.pk}).json()
        self.assertFalse(result['error'], result)
        self.assertFalse(Host.objects.filter(pk=self.ha.pk).exists())

    @patch('apps.host.models.Host.get_ssh')
    def test_extend_rejects_foreign_host_before_ssh(self, ssh):
        self.assert_denied(self.call('/host/extend/', data={'host_id': self.hb.pk}))
        ssh.assert_not_called()

    @patch('apps.host.views.Thread')
    def test_import_over_quota_rolls_back_all_valid_candidates(self, thread):
        AccountResourcePolicy.objects.filter(user=self.a).update(server_limit=2)
        wb = Workbook()
        wb.active.title = 'Sheet1'
        wb.active.append(['name', 'host', 'port', 'username', 'password', 'desc'])
        for n in (1, 2):
            wb.active.append(['import-%s' % n, '192.0.2.%s' % (n + 20), 22, 'root', 'password', ''])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = self.client.post('/host/import/', {'group_id': self.ga.pk, 'file': buf},
                                    HTTP_X_TOKEN=self.a.access_token)
        self.assert_denied(response)
        self.assertFalse(Host.objects.filter(name__startswith='import-').exists())
        thread.assert_not_called()

    @patch('apps.file.views.Host.get_ssh')
    def test_read_grant_cannot_delete_remote_file(self, ssh):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        self.assert_denied(self.call('/file/object/', 'delete', {'id': self.hb.pk, 'file': '/tmp/file'}))
        ssh.assert_not_called()

    @patch('apps.exec.views.get_redis_connection')
    def test_exec_retry_rejects_other_owners_token(self, redis):
        task = self.history(self.b, [self.ha.pk])
        self.assert_denied(self.call('/exec/do/', 'patch', {'token': task.digest}))
        redis.return_value.rpush.assert_not_called()

    @patch('apps.exec.views.get_redis_connection')
    def test_exec_retry_rejects_one_unauthorized_target_as_whole(self, redis):
        task = self.history(hosts=[self.ha.pk, self.hb.pk])
        self.assert_denied(self.call('/exec/do/', 'patch', {'token': task.digest, 'host_ids': [self.ha.pk]}))
        redis.return_value.rpush.assert_not_called()

    def test_history_hides_mixed_scope_records(self):
        self.history(hosts=[self.ha.pk, self.hb.pk])
        self.assertEqual([], self.call('/exec/do/').json()['data'])

    def test_template_list_only_self_and_admin_public(self):
        for user in (self.a, self.b, self.admin):
            ExecTemplate.objects.create(name=user.username, type='shell', body='true', created_by=user)
        names = [x['name'] for x in self.call('/exec/template/').json()['data']['templates']]
        self.assertEqual(set(names), {'a', 'admin'})

    def test_foreign_template_cannot_be_deleted(self):
        tpl = ExecTemplate.objects.create(name='foreign', type='shell', body='true', created_by=self.b)
        self.assert_denied(self.call('/exec/template/', 'delete', {'id': tpl.pk}))
        self.assertTrue(ExecTemplate.objects.filter(pk=tpl.pk).exists())

    @patch('apps.exec.transfer.Thread')
    def test_transfer_retry_rejects_foreign_record(self, thread):
        task = Transfer.objects.create(user=self.b, digest='f' * 32, src_dir='/tmp/a', dst_dir='/tmp/b',
                                       host_ids=json.dumps([self.ha.pk]))
        self.assert_denied(self.call('/exec/transfer/', 'patch', {'token': task.digest}))
        thread.assert_not_called()

    def test_auth_decorator_ignores_stale_permission_cache(self):
        self.a.set_perms_cache({'exec.task.do'})
        self.a.roles.clear()
        self.assert_denied(self.call('/exec/do/'))

    def test_ssh_consumer_rejects_read_grant_and_missing_feature(self):
        from consumer.consumers import SSHConsumer
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        ws = SSHConsumer()
        ws.user, ws.scope = self.a, {'url_route': {'kwargs': {'id': self.hb.pk}}}
        ws.send = MagicMock()
        ws.close_with_message = MagicMock()
        with patch.object(Host, 'get_ssh') as ssh, patch('consumer.consumers.Thread'):
            ws.init()
        ws.close_with_message.assert_called()
        ssh.assert_not_called()

    def test_ssh_receive_rechecks_revoked_feature(self):
        from consumer.consumers import SSHConsumer
        ws = SSHConsumer()
        ws.user, ws.id, ws.chan = self.a, self.ha.pk, MagicMock()
        ws.close_with_message = MagicMock()
        self.a.roles.clear()
        ws.receive(text_data=json.dumps({'data': 'command'}))
        ws.chan.send.assert_not_called()
        ws.close_with_message.assert_called()

    @patch('consumer.consumers.get_redis_connection')
    def test_com_consumer_cannot_read_guessed_token(self, redis):
        from consumer.consumers import ComConsumer
        ws = ComConsumer()
        ws.user, ws.scope = self.a, {'url_route': {'kwargs': {'module': 'raw', 'token': 'guessed'}}}
        ws.close_with_message = MagicMock()
        ws.init()
        ws.close_with_message.assert_called()
        redis.return_value.lindex.assert_not_called()

    @patch('apps.file.views.get_redis_connection')
    def test_upload_ticket_is_issued_by_server_for_authorized_host(self, redis):
        response = self.call('/file/object/', 'put', {'id': self.ha.pk})
        self.assertFalse(response.json()['error'], response.content)
        self.assertEqual(len(response.json()['data']), 32)
        self.assertTrue(redis.return_value.set.called)

    @patch('apps.file.views.get_redis_connection')
    @patch('apps.file.views.Host.get_ssh')
    def test_upload_cannot_assign_itself_an_existing_output_token(self, ssh, redis):
        redis.return_value.get.return_value = None
        response = self.client.post('/file/object/', {'id': self.ha.pk, 'path': '/tmp',
            'token': 'arbitrary-other-output', 'file': io.BytesIO(b'content')},
            HTTP_X_TOKEN=self.a.access_token)
        self.assert_denied(response)
        ssh.assert_not_called()

    def test_delete_cannot_split_other_account_linked_panels_over_quota(self):
        from apps.baota.models import Panel, PanelGroup
        from apps.baota.resource_monitor.models import ResourceMonitorPreference
        pg = PanelGroup.objects.create(name='panels')
        for n in (1, 2):
            panel = Panel.objects.create(name='panel-%s' % n, url='http://192.0.2.11',
                username='root', password_ciphertext='', group=pg, created_by=self.b)
            ResourceMonitorPreference.objects.create(panel=panel, linked_host=self.ha)
        AccountResourcePolicy.objects.create(user=self.b, server_limit=2)  # one SSH + paired panel group
        self.assert_denied(self.call('/host/', 'delete', {'id': self.ha.pk}))
        self.assertTrue(Host.objects.filter(pk=self.ha.pk).exists())
        self.assertEqual(ResourceMonitorPreference.objects.filter(linked_host=self.ha).count(), 2)

    @patch('apps.host.views._do_host_verify', return_value=True)
    def test_authorized_add_sets_owner_and_groups_without_exposing_key(self, verify):
        result = self.call('/host/', 'post', self.form()).json()
        self.assertFalse(result['error'], result)
        host = Host.objects.get(name='new')
        self.assertEqual(host.created_by_id, self.a.pk)
        self.assertEqual(list(host.groups.values_list('pk', flat=True)), [self.ga.pk])
        self.assertNotIn('explicit-private', str(result))

    def test_admin_still_sees_all_hosts(self):
        data = self.call('/host/', user=self.admin).json()['data']
        self.assertEqual({self.ha.pk, self.hb.pk}, {x['id'] for x in data})

    @patch('apps.file.views.fetch_dir_list', return_value=[])
    def test_explicit_read_share_can_list_files(self, listing):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        result = self.call('/file/', data={'id': self.hb.pk, 'path': '/tmp'}).json()
        self.assertFalse(result['error'], result)
        listing.assert_called_once()

    def test_created_group_gets_owner_and_cannot_claim_other_group(self):
        result = self.call('/host/group/', 'post', {'name': 'own new'}).json()
        self.assertFalse(result['error'], result)
        group = Group.objects.get(name='own new')
        self.assertTrue(ResourceGroupOwner.objects.filter(kind='host', group_id=group.pk, owner=self.a).exists())
        self.assert_denied(self.call('/host/group/', 'post', {'id': self.gb.pk, 'name': 'stolen'}))

    def test_worker_rechecks_revoked_account_before_creating_ssh(self):
        from apps.exec.executors import Job
        record = self.history()
        self.a.is_active = False
        self.a.save()
        with patch('apps.exec.executors.SSH') as ssh:
            with self.assertRaises(access.AccessDenied):
                Job(token=record.digest, key=self.ha.pk, name='queued', hostname='192.0.2.1',
                    port=22, username='root', pkey='stale', command='true', interpreter='sh', actor_id=self.a.pk)
        ssh.assert_not_called()

    @patch('apps.exec.executors.get_redis_connection')
    @patch('apps.exec.executors.SSH')
    def test_worker_rechecks_right_before_remote_dispatch(self, ssh, redis):
        from apps.exec.executors import Job
        record = self.history()
        job = Job(token=record.digest, key=self.ha.pk, name='queued', hostname='192.0.2.1',
                  port=22, username='root', pkey='stale', command='true', interpreter='sh', actor_id=self.a.pk)
        self.a.roles.clear()
        with self.assertRaises(access.AccessDenied):
            job.run()
        ssh.return_value.__enter__.assert_not_called()

    def test_pubsub_consumer_cannot_subscribe_other_owners_task(self):
        from consumer.consumers import PubSubConsumer
        task = self.history(self.b, [self.ha.pk])
        ws = PubSubConsumer()
        ws.user, ws.scope = self.a, {'url_route': {'kwargs': {'token': task.digest}}}
        ws.close_with_message = MagicMock()
        with patch('consumer.consumers.get_redis_connection') as redis:
            ws.init()
            redis.return_value.pubsub.assert_not_called()
        ws.close_with_message.assert_called()

    def test_pubsub_receive_rechecks_revoked_grant_before_output(self):
        from consumer.consumers import PubSubConsumer
        grant = ResourceGrant.objects.create(user=self.a, host=self.hb, level='manage')
        task = self.history(hosts=[self.ha.pk, self.hb.pk])
        ws = PubSubConsumer()
        ws.user, ws.token, ws.rds, ws.p = self.a, task.digest, MagicMock(), MagicMock()
        ws.close_with_message, ws.send = MagicMock(), MagicMock()
        grant.delete()
        ws.receive()
        ws.p.get_message.assert_not_called()
        ws.send.assert_not_called()
        ws.close_with_message.assert_called()

    @patch('apps.host.add.utils.fetch_ali_instances')
    def test_cloud_import_rolls_back_all_hosts_when_quota_is_short(self, fetch):
        AccountResourcePolicy.objects.filter(user=self.a).update(server_limit=2)
        fetch.return_value = [dict(instance_id='i-%s' % n, instance_name='cloud-%s' % n,
            public_ip_address=['192.0.2.%s' % (40 + n)], private_ip_address=[],
            os_name='Linux', os_type='linux', cpu=1, memory=1,
            instance_charge_type='Other', internet_charge_type='Other') for n in (1, 2)]
        result = self.call('/host/import/cloud/', 'post', dict(type='ali', ak='fixture', ac='secret',
            region_id='fixture', group_id=self.ga.pk, username='root', port=22, host_type='public'))
        self.assert_denied(result)
        self.assertFalse(Host.objects.filter(name__startswith='cloud-').exists())

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key')
    def test_password_verification_creates_independent_key_for_ordinary_owner(self, global_key, ssh):
        ssh.generate_key.return_value = ('unique-private', 'unique-public')
        data = self.form(password='supplied-password')
        data['pkey'] = None
        result = self.call('/host/', 'post', data).json()
        self.assertFalse(result['error'], result)
        global_key.assert_not_called()
        self.assertEqual(Host.objects.get(name='new').pkey, 'unique-private')
        ssh.return_value.__enter__.return_value.add_public_key.assert_called_once_with('unique-public')

    @patch('apps.file.views.Host.get_ssh')
    def test_authorized_upload_uses_only_server_issued_ticket(self, ssh):
        store = {}
        redis = MagicMock()
        redis.set.side_effect = lambda k, value, **kw: store.update({k: value})
        redis.get.side_effect = lambda k: store.get(k)
        with patch('apps.file.views.get_redis_connection', return_value=redis):
            token = self.call('/file/object/', 'put', {'id': self.ha.pk}).json()['data']
            result = self.client.post('/file/object/', {'id': self.ha.pk, 'path': '/tmp',
                'token': token, 'file': io.BytesIO(b'content')}, HTTP_X_TOKEN=self.a.access_token).json()
        self.assertFalse(result['error'], result)
        ssh.return_value.__enter__.return_value.put_file_by_fl.assert_called_once()

    def test_explicit_management_share_cannot_change_foreign_group_membership(self):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='manage')
        with patch('apps.host.views._do_host_verify', return_value=True):
            result = self.call('/host/', 'post', self.form(id=self.hb.pk)).json()
        self.assertTrue(result['error'], result)
        self.assertIn(self.gb.pk, self.hb.groups.values_list('pk', flat=True))
        self.assertNotIn(self.ga.pk, self.hb.groups.values_list('pk', flat=True))

    def test_foreign_template_snapshot_is_not_exposed_by_legacy_history(self):
        tpl = ExecTemplate.objects.create(name='private', type='shell', body='foreign-private-body',
                                         created_by=self.b)
        record = self.history()
        record.template = tpl
        record.save()
        result = self.call('/exec/do/').json()
        self.assertNotIn('foreign-private-body', str(result))

    def test_notify_consumer_discards_unscoped_broadcast_for_ordinary_user(self):
        from consumer.consumers import NotifyConsumer
        ws = NotifyConsumer()
        ws.user, ws.base_send = self.a, MagicMock()
        ws.notify_message({'type': 'notify.message', 'title': 'other host', 'content': 'foreign-secret'})
        ws.base_send.assert_not_called()

    def test_ssh_output_rechecks_after_network_read(self):
        from consumer.consumers import SSHConsumer
        ws = SSHConsumer()
        ws.user, ws.id, ws.chan, ws.base_send = self.a, self.ha.pk, MagicMock(), MagicMock()
        def read(_size):
            self.a.roles.clear()
            return b'private-output'
        ws.chan.recv.side_effect = read
        ws.chan.recv_ready.return_value = False
        ws.loop_read()
        self.assertNotIn('private-output', str(ws.base_send.call_args_list))
        self.assertTrue(ws.closed)

    def test_ssh_idle_loop_rechecks_disabled_account(self):
        import socket
        from consumer.consumers import SSHConsumer
        ws = SSHConsumer()
        ws.user, ws.id, ws.chan, ws.base_send = self.a, self.ha.pk, MagicMock(), MagicMock()
        def timeout(_size):
            User.objects.filter(pk=self.a.pk).update(is_active=False)
            raise socket.timeout()
        ws.chan.recv.side_effect = timeout
        ws.loop_read()
        self.assertTrue(ws.closed)
        ws.chan.close.assert_called()

    # Direct response fixtures have no request lifecycle; emitting its finish
    # signal here would close MariaDB's TestCase-owned outer transaction.
    @patch('django.http.response.signals.request_finished.send')
    def test_download_stream_stops_before_next_chunk_after_grant_revocation(self, finished):
        from apps.file.utils import FileResponseAfter
        grant = ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        callback = MagicMock()
        response = FileResponseAfter(callback, iter([b'first', b'second']), user=self.a, host_id=self.hb.pk)
        chunks = iter(response.streaming_content)
        self.assertEqual(next(chunks), b'first')
        grant.delete()
        with self.assertRaises(access.AccessDenied):
            next(chunks)
        callback.assert_called()

    @patch('apps.exec.views.get_redis_connection')
    def test_terminate_rejects_other_owners_token_before_reading_process_key(self, redis):
        record = self.history(self.b, [self.ha.pk])
        self.assert_denied(self.call('/exec/terminate/', 'post', {'token': record.digest, 'target': str(self.ha.pk)}))
        redis.return_value.get.assert_not_called()

    @patch('apps.exec.views.get_redis_connection')
    def test_read_shared_host_cannot_be_queued_for_command(self, redis):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        self.assert_denied(self.call('/exec/do/', 'post', {'host_ids': [self.hb.pk], 'command': 'true'}))
        self.assertFalse(ExecHistory.objects.exists())
        redis.return_value.rpush.assert_not_called()

    @patch('apps.exec.views.require_stream')
    @patch('apps.exec.views.get_redis_connection')
    def test_exec_permission_does_not_allow_terminating_build_process(self, redis, stream):
        from django.conf import settings
        self.perms(self.a, 'exec.task.do')
        stream.return_value = self.a
        self.assert_denied(self.call('/exec/terminate/', 'post', {
            'token': settings.BUILD_KEY + ':42', 'target': 'local'}))
        redis.return_value.get.assert_not_called()

    @patch('consumer.consumers.get_redis_connection')
    def test_pipeline_output_checks_transfer_source_even_without_display_target(self, redis):
        from apps.pipeline.models import Pipeline, PipeHistory
        from consumer.consumers import ComConsumer
        self.perms(self.a, 'pipeline.pipeline.view')
        pipe = Pipeline.objects.create(name='pipe', created_by=self.a)
        record = PipeHistory.objects.create(pipeline=pipe, ordinal=1, token='p' * 32, created_by=self.a,
            nodes=json.dumps([{'module': 'data_transfer', 'source': {'target': self.hb.pk},
                               'destination': {'targets': [self.ha.pk]}, '_targets': [{'id': self.ha.pk}]}]))
        ws = ComConsumer()
        ws.user, ws.scope = self.a, {'url_route': {'kwargs': {'module': 'pipeline', 'token': record.token}}}
        ws.close_with_message = MagicMock()
        ws.init()
        ws.close_with_message.assert_called()

    def test_read_grant_host_list_does_not_expose_hidden_group_ids(self):
        hidden = Group.objects.create(name='secret-team')
        ResourceGroupOwner.objects.create(kind='host', group_id=hidden.pk, owner=self.b)
        self.hb.groups.add(hidden)
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        rows = self.call('/host/').json()['data']
        shared = next(row for row in rows if row['id'] == self.hb.pk)
        self.assertNotIn(hidden.pk, shared['group_ids'])

    def test_denied_remote_file_operation_records_actor_without_credentials(self):
        from apps.account.models import ResourceAccessAudit
        self.assert_denied(self.call('/file/object/', 'delete', {'id': self.hb.pk, 'file': '/tmp/file'}))
        audit = ResourceAccessAudit.objects.filter(actor=self.a, event='resource_denied').first()
        self.assertIsNotNone(audit)
        self.assertNotIn('secret-', str(audit.detail))

    def repository(self):
        from apps.app.models import App, Deploy
        from apps.config.models import Environment
        from apps.repository.models import Repository
        app = App.objects.create(name='app', key='app', created_by=self.admin)
        env = Environment.objects.create(name='env', key='env', created_by=self.admin)
        deploy = Deploy.objects.create(app=app, env=env, host_ids=json.dumps([self.ha.pk]),
                                       extend='1', is_audit=False, created_by=self.admin)
        return Repository.objects.create(app=app, env=env, deploy=deploy, version='fixture',
                                         spug_version='fixture', extra='[]', created_by=self.a)

    def test_build_output_also_requires_existing_app_and_environment_access(self):
        from apps.account.utils import require_stream
        self.perms(self.a, 'deploy.repository.view')
        record = self.repository()
        with self.assertRaises(access.AccessDenied):
            require_stream(self.a, record.deploy_key, MagicMock(), module='raw')

    def test_raw_build_output_works_for_owner_with_complete_permissions(self):
        from apps.account.utils import require_stream
        self.perms(self.a, 'deploy.repository.view')
        record = self.repository()
        role = self.a.roles.first()
        role.deploy_perms = {'apps': [record.app_id], 'envs': [record.env_id]}
        role.save()
        self.assertEqual(require_stream(self.a, record.deploy_key, MagicMock(), module='raw').pk, self.a.pk)

    def test_file_list_rechecks_permissions_after_remote_read(self):
        def fetch(*args, **kwargs):
            self.a.roles.clear()
            return [{'name': 'secret-output'}]
        with patch('apps.file.views.fetch_dir_list', side_effect=fetch):
            response = self.call('/file/', data={'id': self.ha.pk, 'path': '/tmp'})
        self.assert_denied(response)
        self.assertNotIn('secret-output', str(response.content))

    def test_file_delete_rechecks_after_ssh_connection_before_remote_action(self):
        remote = MagicMock()
        def enter():
            self.a.roles.clear()
            return remote
        with patch.object(Host, 'get_ssh') as ssh:
            ssh.return_value.__enter__.side_effect = enter
            self.assert_denied(self.call('/file/object/', 'delete', {'id': self.ha.pk, 'file': '/tmp/file'}))
        remote.remove_file.assert_not_called()

    def test_ssh_init_rechecks_after_connect_before_opening_shell(self):
        from consumer.consumers import SSHConsumer
        ws, remote = SSHConsumer(), MagicMock()
        ws.user, ws.scope, ws.base_send = self.a, {'url_route': {'kwargs': {'id': self.ha.pk}}}, MagicMock()
        def connect():
            self.a.roles.clear()
            return remote
        with patch.object(Host, 'get_ssh') as ssh, patch('consumer.consumers.Thread'):
            ssh.return_value.get_client.side_effect = connect
            ws.init()
        remote.invoke_shell.assert_not_called()

    @patch('apps.exec.executors.get_redis_connection')
    @patch('apps.exec.executors.SSH')
    def test_worker_rechecks_after_connect_before_command_dispatch(self, ssh, redis):
        from apps.exec.executors import Job
        record, remote = self.history(), MagicMock()
        job = Job(token=record.digest, key=self.ha.pk, name='queued', hostname='192.0.2.1',
                  port=22, username='root', pkey='stale', command='true', interpreter='sh', actor_id=self.a.pk)
        def enter():
            self.a.roles.clear()
            return remote
        ssh.return_value.__enter__.side_effect = enter
        try:
            job.run()
        except access.AccessDenied:
            pass
        ssh.return_value.exec_command_with_stream.assert_not_called()

    def test_file_list_rechecks_after_connect_before_remote_directory_action(self):
        remote = MagicMock()
        remote.list_dir_attr.return_value = []
        def enter():
            self.a.roles.clear()
            return remote
        with patch.object(Host, 'get_ssh') as ssh:
            ssh.return_value.__enter__.side_effect = enter
            self.assert_denied(self.call('/file/', data={'id': self.ha.pk, 'path': '/tmp'}))
        remote.list_dir_attr.assert_not_called()

    @patch('apps.host.views.SSH')
    def test_host_password_validation_rechecks_before_installing_public_key(self, ssh):
        remote = MagicMock()
        ssh.generate_key.return_value = ('independent-private', 'independent-public')
        def enter():
            self.a.roles.clear()
            return remote
        ssh.return_value.__enter__.side_effect = enter
        data = self.form(password='fixture-password')
        data['pkey'] = None
        self.assert_denied(self.call('/host/', 'post', data))
        remote.add_public_key.assert_not_called()

    def test_transfer_source_revocation_prevents_new_sshfs_connection(self):
        remote = MagicMock()
        def validate(_cmd):
            self.a.roles.clear()
            return 0, ''
        remote.exec_command_raw.side_effect = validate
        with patch.object(Host, 'get_ssh') as ssh, patch('apps.exec.transfer.os.makedirs'), \
                patch('apps.exec.transfer.subprocess.run') as run:
            ssh.return_value.__enter__.return_value = remote
            run.return_value.returncode = 0
            response = self.client.post('/exec/transfer/', {'data': json.dumps({
                'host': [self.ha.pk, '/tmp/src'], 'host_ids': [self.ha.pk], 'dst_dir': '/tmp/dst'})},
                HTTP_X_TOKEN=self.a.access_token)
            self.assert_denied(response)
        run.assert_not_called()

    def test_batch_verify_rechecks_all_original_targets_before_each_host(self):
        from apps.host.utils import batch_sync_host
        grant = ResourceGrant.objects.create(user=self.a, host=self.hb, level='manage')
        def submit(function, *args, **kwargs):
            ResourceGrant.objects.filter(user=self.a, host=self.hb).delete()
            future = MagicMock()
            try:
                function(*args, **kwargs)
                future.exception.return_value = None
            except access.AccessDenied as exc:
                future.exception.return_value = exc
            return future
        with patch('apps.host.utils.get_redis_connection'), patch('apps.host.utils._sync_host_extend') as sync, \
                patch('apps.host.utils.futures.ThreadPoolExecutor') as pool, \
                patch('apps.host.utils.futures.as_completed', return_value=[]):
            pool.return_value.__enter__.return_value.submit.side_effect = submit
            batch_sync_host('fixture', [self.ha, self.hb], user_id=self.a.pk)
        sync.assert_not_called()

    @patch('django.http.response.signals.request_finished.send')
    def test_download_does_not_read_next_remote_chunk_after_revocation(self, finished):
        from apps.file.utils import FileResponseAfter
        reads = []
        def remote_chunks():
            for chunk in (b'first', b'second'):
                reads.append(chunk)
                yield chunk
        grant = ResourceGrant.objects.create(user=self.a, host=self.hb, level='read')
        response = FileResponseAfter(MagicMock(), remote_chunks(), user=self.a, host_id=self.hb.pk)
        chunks = iter(response.streaming_content)
        self.assertEqual(next(chunks), b'first')
        grant.delete()
        with self.assertRaises(access.AccessDenied):
            next(chunks)
        self.assertEqual(reads, [b'first'])

    def test_host_detail_collection_rechecks_between_remote_commands(self):
        self.ha.is_verified = True
        self.ha.save()
        remote = MagicMock()
        remote.arguments = {'hostname': self.ha.hostname}
        def first_command(_cmd):
            self.a.roles.clear()
            return 0, '1'
        remote.exec_command_raw.side_effect = first_command
        with patch.object(Host, 'get_ssh') as ssh:
            ssh.return_value.__enter__.return_value = remote
            self.assert_denied(self.call('/host/extend/', data={'host_id': self.ha.pk}))
        self.assertEqual(remote.exec_command_raw.call_count, 1)

    def test_transfer_source_output_is_not_returned_after_revocation(self):
        remote = MagicMock()
        remote.exec_command_raw.return_value = (0, '')
        def mount(*args, **kwargs):
            self.a.roles.clear()
            return MagicMock(returncode=1, stdout=b'private-remote-stderr')
        with patch.object(Host, 'get_ssh') as ssh, patch('apps.exec.transfer.os.makedirs'), \
                patch('apps.exec.transfer.remove_mount_dir'), \
                patch('apps.exec.transfer.subprocess.run', side_effect=mount):
            ssh.return_value.__enter__.return_value = remote
            response = self.client.post('/exec/transfer/', {'data': json.dumps({
                'host': [self.ha.pk, '/tmp/src'], 'host_ids': [self.ha.pk], 'dst_dir': '/tmp/dst'})},
                HTTP_X_TOKEN=self.a.access_token)
        self.assert_denied(response)
        self.assertNotIn('private-remote-stderr', str(response.content))

    def test_termination_rechecks_after_connect_before_remote_kill(self):
        record, remote = self.history(), MagicMock()
        def enter():
            self.a.roles.clear()
            return remote
        with patch.object(Host, 'get_ssh') as ssh, patch('apps.exec.views.get_redis_connection') as redis:
            ssh.return_value.__enter__.side_effect = enter
            redis.return_value.get.return_value = b'123'
            self.assert_denied(self.call('/exec/terminate/', 'post', {'token': record.digest, 'target': str(self.ha.pk)}))
        remote.terminate.assert_not_called()

    def test_transfer_dispatch_rechecks_immediately_before_rsync_process(self):
        from apps.exec.transfer import _do_sync
        task = Transfer.objects.create(user=self.a, digest='f' * 32, src_dir='/tmp/a', dst_dir='/tmp/b',
                                       host_ids=json.dumps([self.ha.pk]))
        def enter():
            self.a.roles.clear()
            return MagicMock()
        with patch('apps.exec.transfer.tempfile.NamedTemporaryFile') as temp, \
                patch('apps.exec.transfer.subprocess.Popen') as process:
            temp.return_value.__enter__.side_effect = enter
            process.return_value.stdout.read.return_value = b''
            process.return_value.wait.return_value = 0
            try:
                _do_sync(MagicMock(), task, self.ha)
            except access.AccessDenied:
                pass
        process.assert_not_called()

    def test_shared_manager_can_edit_connection_with_empty_groups(self):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='manage')
        ResourceGroupOwner.objects.filter(owner=self.a).delete()
        self.perms(self.a, 'host.host.edit')
        data = self.form(id=self.hb.pk)
        data['group_ids'] = []
        with patch('apps.host.views._do_host_verify', return_value=True):
            result = self.call('/host/', 'post', data).json()
        self.assertFalse(result['error'], result)
        self.hb.refresh_from_db()
        self.assertEqual(self.hb.hostname, data['hostname'])
        self.assertEqual(set(self.hb.groups.values_list('pk', flat=True)), {self.gb.pk})

    def test_shared_manager_cannot_move_host_between_owned_groups(self):
        ResourceGrant.objects.create(user=self.a, host=self.hb, level='manage')
        self.hb.groups.add(self.ga)
        group = Group.objects.create(name='other-own')
        ResourceGroupOwner.objects.create(kind='host', group_id=group.pk, owner=self.a)
        self.assert_denied(self.call('/host/', 'patch', {'host_ids': [self.hb.pk],
            's_group_id': self.ga.pk, 't_group_id': group.pk, 'is_copy': False}))
        self.assertNotIn(group.pk, self.hb.groups.values_list('pk', flat=True))

    def test_transferred_actual_owner_can_add_host_to_owned_group(self):
        from apps.account.models import ResourceOwnership
        ResourceOwnership.objects.create(host=self.hb, owner=self.a)
        with patch('apps.host.views._do_host_verify', return_value=True):
            result = self.call('/host/', 'post', self.form(id=self.hb.pk)).json()
        self.assertFalse(result['error'], result)
        self.assertIn(self.ga.pk, self.hb.groups.values_list('pk', flat=True))

    def test_ssh_continuous_output_stops_reading_after_revocation(self):
        from consumer.consumers import SSHConsumer
        ws = SSHConsumer()
        ws.user, ws.id, ws.chan, ws.base_send = self.a, self.ha.pk, MagicMock(), MagicMock()
        def read(_size):
            self.a.roles.clear()
            return b'private-output'
        ws.chan.recv.side_effect = read
        ws.chan.recv_ready.side_effect = [True, False]
        ws.loop_read()
        self.assertEqual(ws.chan.recv.call_count, 1)

    def test_group_move_cannot_implicitly_move_foreign_descendants(self):
        self.gb.parent_id = self.ga.pk
        self.gb.save()
        target = Group.objects.create(name='own-target')
        ResourceGroupOwner.objects.create(kind='host', group_id=target.pk, owner=self.a)
        self.assert_denied(self.call('/host/group/', 'patch', {'s_id': self.ga.pk, 'd_id': target.pk, 'action': 0}))
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.parent_id, 0)

    def test_feature_permission_denial_is_audited_without_request_secrets(self):
        from apps.account.models import ResourceAccessAudit
        self.perms(self.a, 'host.host.view')
        self.assert_denied(self.call('/file/object/', 'delete', {'id': self.ha.pk,
                           'file': '/tmp/request-secret'}))
        audit = ResourceAccessAudit.objects.filter(actor=self.a, event='permission_denied').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.detail.get('codes'), ['host.console.del'])
        self.assertNotIn('request-secret', str(audit.detail))
        self.assertNotIn(self.a.access_token, str(audit.detail))

    def test_upload_rechecks_after_ticket_read_before_new_ssh_connection(self):
        def ticket(_key):
            self.a.roles.clear()
            return json.dumps({'owner_id': self.a.pk, 'host_ids': [self.ha.pk], 'code': 'host.console.upload'})
        with patch.object(Host, 'get_ssh') as ssh, patch('apps.file.views.get_redis_connection') as redis:
            redis.return_value.get.side_effect = ticket
            response = self.client.post('/file/object/', {'id': self.ha.pk, 'path': '/tmp',
                'token': 's' * 32, 'file': io.BytesIO(b'content')}, HTTP_X_TOKEN=self.a.access_token)
            self.assert_denied(response)
        ssh.assert_not_called()

    def test_termination_rechecks_after_pid_read_before_new_ssh_connection(self):
        record = self.history()
        def pid(_key):
            self.a.roles.clear()
            return b'123'
        with patch.object(Host, 'get_ssh') as ssh, patch('apps.exec.views.get_redis_connection') as redis:
            redis.return_value.get.side_effect = pid
            self.assert_denied(self.call('/exec/terminate/', 'post', {'token': record.digest, 'target': str(self.ha.pk)}))
        ssh.assert_not_called()

    @patch('apps.exec.executors.get_redis_connection')
    @patch('apps.exec.executors.SSH')
    def test_worker_rechecks_after_initial_publish_before_new_ssh_connection(self, ssh, redis):
        from apps.exec.executors import Job
        record = self.history()
        job = Job(token=record.digest, key=self.ha.pk, name='queued', hostname='192.0.2.1',
                  port=22, username='root', pkey='stale', command='true', interpreter='sh', actor_id=self.a.pk)
        redis.return_value.publish.side_effect = lambda *args: self.a.roles.clear()
        try:
            job.run()
        except access.AccessDenied:
            pass
        ssh.return_value.__enter__.assert_not_called()

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_admin_edit_ordinary_creator_host_installs_usable_independent_key(self, global_key, ssh):
        self.ha.pkey = None
        self.ha.save()
        ssh.generate_key.return_value = ('independent-private', 'independent-public')
        data = self.form(id=self.ha.pk, password='fixture-password')
        data['pkey'] = None
        result = self.call('/host/', 'post', data, user=self.admin).json()
        self.assertFalse(result['error'], result)
        global_key.assert_not_called()
        self.ha.refresh_from_db()
        self.assertEqual(self.ha.private_key, 'independent-private')
        self.assertTrue(self.ha.is_verified)

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_admin_cannot_validate_ordinary_creator_host_without_own_credentials(self, global_key, ssh):
        self.ha.pkey = None
        self.ha.save()
        data = self.form(id=self.ha.pk)
        data['pkey'] = None
        self.assert_denied(self.call('/host/', 'post', data, user=self.admin))
        global_key.assert_not_called()
        ssh.assert_not_called()

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_shared_global_host_cannot_redirect_global_key_to_a_new_endpoint(self, global_key, ssh):
        host = self.host('admin-shared', self.admin, self.ga)
        host.pkey = None
        host.save()
        ResourceGrant.objects.create(user=self.a, host=host, level='manage')
        for changes in ({'hostname': '192.0.2.200'}, {'port': 2222}, {'username': 'other-user'}):
            with self.subTest(changes=changes):
                data = self.form(id=host.pk)
                data.update(hostname=host.hostname, port=host.port, username=host.username, pkey=None, group_ids=[])
                data.update(changes)
                self.assert_denied(self.call('/host/', 'post', data))
        global_key.assert_not_called()
        ssh.assert_not_called()
        host.refresh_from_db()
        self.assertEqual((host.hostname, host.port, host.username), ('192.0.2.%s' % self.admin.pk, 22, 'root'))

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_shared_global_host_new_endpoint_password_installs_independent_key(self, global_key, ssh):
        host = self.host('admin-shared', self.admin, self.ga)
        host.pkey = None
        host.save()
        ResourceGrant.objects.create(user=self.a, host=host, level='manage')
        ssh.generate_key.return_value = ('independent-private', 'independent-public')
        data = self.form(id=host.pk, password='fixture-password')
        data.update(pkey=None, group_ids=[])
        result = self.call('/host/', 'post', data).json()
        self.assertFalse(result['error'], result)
        global_key.assert_not_called()
        host.refresh_from_db()
        self.assertEqual(host.private_key, 'independent-private')
        self.assertEqual(host.hostname, data['hostname'])
        ssh.return_value.__enter__.return_value.add_public_key.assert_called_once_with('independent-public')

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_shared_global_host_same_endpoint_remains_usable_for_manager(self, global_key, ssh):
        host = self.host('admin-shared', self.admin, self.ga)
        host.pkey = None
        host.save()
        ResourceGrant.objects.create(user=self.a, host=host, level='manage')
        data = self.form(id=host.pk)
        data.update(hostname=host.hostname, port=host.port, username=host.username, pkey=None, group_ids=[])
        result = self.call('/host/', 'post', data).json()
        self.assertFalse(result['error'], result)
        global_key.assert_called_once()
        ssh.assert_called_once()
        host.refresh_from_db()
        self.assertIsNone(host.pkey)

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_shared_independent_host_cannot_be_switched_to_global_by_manager(self, global_key, ssh):
        host = self.host('admin-shared', self.admin, self.ga)
        ResourceGrant.objects.create(user=self.a, host=host, level='manage')
        data = self.form(id=host.pk)
        data.update(hostname=host.hostname, port=host.port, username=host.username, pkey=None, group_ids=[])
        self.assert_denied(self.call('/host/', 'post', data))
        global_key.assert_not_called()
        ssh.assert_not_called()

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_shared_global_host_password_at_same_endpoint_still_installs_independent_key(self, global_key, ssh):
        host = self.host('admin-shared', self.admin, self.ga)
        host.pkey = None
        host.save()
        ResourceGrant.objects.create(user=self.a, host=host, level='manage')
        ssh.generate_key.return_value = ('independent-private', 'independent-public')
        data = self.form(id=host.pk, password='fixture-password')
        data.update(hostname=host.hostname, port=host.port, username=host.username, pkey=None, group_ids=[])
        result = self.call('/host/', 'post', data).json()
        self.assertFalse(result['error'], result)
        global_key.assert_not_called()
        host.refresh_from_db()
        self.assertEqual(host.pkey, 'independent-private')

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key')
    def test_admin_downgrade_after_global_key_read_blocks_new_endpoint_connection(self, global_key, ssh):
        self.perms(self.admin, 'host.host.edit')
        host = self.host('admin-shared', self.admin, self.ga)
        host.pkey = None
        host.save()
        def key():
            User.objects.filter(pk=self.admin.pk).update(is_supper=False)
            return 'global-private', 'global-public'
        global_key.side_effect = key
        data = self.form(id=host.pk)
        data.update(pkey=None, group_ids=[])
        self.assert_denied(self.call('/host/', 'post', data, user=self.admin))
        ssh.assert_not_called()
        host.refresh_from_db()
        self.assertEqual(host.hostname, '192.0.2.%s' % self.admin.pk)

    @patch('apps.host.views.SSH')
    @patch('apps.host.views.AppSetting.get_ssh_key', return_value=('global-private', 'global-public'))
    def test_admin_downgrade_after_password_connect_cannot_install_global_key(self, global_key, ssh):
        self.perms(self.admin, 'host.host.edit')
        host = self.host('admin-shared', self.admin, self.ga)
        host.pkey = None
        host.save()
        remote = MagicMock()
        def enter():
            User.objects.filter(pk=self.admin.pk).update(is_supper=False)
            return remote
        ssh.return_value.__enter__.side_effect = enter
        data = self.form(id=host.pk, password='fixture-password')
        data.update(pkey=None, group_ids=[])
        self.assert_denied(self.call('/host/', 'post', data, user=self.admin))
        remote.add_public_key.assert_not_called()
        host.refresh_from_db()
        self.assertEqual(host.hostname, '192.0.2.%s' % self.admin.pk)

    def test_real_ssh_local_polling_does_not_repeat_authorization(self):
        from libs.ssh import SSH
        authorize, channel = MagicMock(), MagicMock()
        guarded = SSH('192.0.2.1', authorize=authorize)._guard(channel)
        for _ in range(50):
            guarded.recv_ready()
            guarded.recv_stderr_ready()
            guarded.exit_status_ready()
            guarded.is_active()
        authorize.assert_not_called()
        authorize.side_effect = access.AccessDenied('revoked')
        with self.assertRaises(access.AccessDenied):
            guarded.recv(4096)
        channel.recv.assert_not_called()
        with self.assertRaises(access.AccessDenied):
            guarded.sendall(b'next input')
        channel.sendall.assert_not_called()

    def test_real_ssh_idle_shell_setup_still_rechecks_authorization(self):
        ssh, client, channel = self.guarded_ssh(), MagicMock(), MagicMock()
        ssh.client = client
        client.invoke_shell.return_value = channel
        channel.recv_ready.return_value = False
        def idle(seconds):
            if seconds == 0.1:
                self.a.roles.clear()
        with patch('libs.ssh.time.sleep', side_effect=idle):
            with self.assertRaises(access.AccessDenied):
                ssh._get_channel()
        channel.recv.assert_not_called()

    @patch('apps.exec.executors.get_redis_connection')
    @patch('libs.ssh.SSH.load_pkey', return_value=None)
    def test_real_worker_cannot_dispatch_after_script_upload_revokes_permission(self, pkey, redis):
        from apps.exec.executors import Job
        record = self.history()
        job = Job(token=record.digest, key=self.ha.pk, name='queued', hostname='192.0.2.1',
                  port=22, username='root', pkey='stale', command='true', interpreter='sh', actor_id=self.a.pk)
        job.ssh.client = MagicMock()
        job.ssh.client.get_transport.return_value.remote_version = 'OpenSSH_9'
        channel = MagicMock()
        channel.recv.return_value = b'Spug EOF 2108111926 0\n'
        channel.recv_ready.return_value = False
        job.ssh.channel, job.ssh.pid = channel, 123
        with patch.object(job.ssh, 'put_file_by_fl', side_effect=lambda *a, **kw: self.a.roles.clear()):
            with self.assertRaises(access.AccessDenied):
                job.run()
        channel.sendall.assert_not_called()

    def guarded_ssh(self):
        from libs.ssh import SSH
        return SSH('192.0.2.1', authorize=lambda: access.require(self.a, 'host.console.upload'))

    def test_real_ssh_rechecks_after_open_session_before_command(self):
        ssh, client = self.guarded_ssh(), MagicMock()
        channel = MagicMock()
        def open_session():
            self.a.roles.clear()
            return channel
        client.get_transport.return_value.open_session.side_effect = open_session
        ssh.client = client
        with self.assertRaises(access.AccessDenied):
            ssh.exec_command_raw('private command')
        channel.exec_command.assert_not_called()

    def test_real_ssh_rechecks_after_sftp_open_before_remove(self):
        ssh, client, sftp = self.guarded_ssh(), MagicMock(), MagicMock()
        def open_sftp():
            self.a.roles.clear()
            return sftp
        client.open_sftp.side_effect = open_sftp
        ssh.client = client
        with self.assertRaises(access.AccessDenied):
            ssh.remove_file('/tmp/private')
        sftp.remove.assert_not_called()

    def test_real_ssh_rechecks_after_sftp_open_before_listing(self):
        ssh, client, sftp = self.guarded_ssh(), MagicMock(), MagicMock()
        def open_sftp():
            self.a.roles.clear()
            return sftp
        client.open_sftp.side_effect = open_sftp
        ssh.client = client
        with self.assertRaises(access.AccessDenied):
            ssh.list_dir_attr('/tmp/private')
        sftp.listdir_attr.assert_not_called()

    def test_real_ssh_rechecks_after_remote_file_open_before_write(self):
        ssh, sftp, remote = self.guarded_ssh(), MagicMock(), MagicMock()
        def open_remote(*args):
            self.a.roles.clear()
            return remote
        sftp.open.side_effect = open_remote
        ssh.sftp = sftp
        with self.assertRaises(access.AccessDenied):
            ssh.put_file_by_fl(io.BytesIO(b'private'), '/tmp/private')
        remote.write.assert_not_called()
        remote.close.assert_called_once()

    def test_real_ssh_upload_stops_before_next_chunk_after_revocation(self):
        ssh, sftp, remote = self.guarded_ssh(), MagicMock(), MagicMock()
        sftp.open.return_value = remote
        remote.write.side_effect = lambda data: self.a.roles.clear()
        ssh.sftp = sftp
        with self.assertRaises(access.AccessDenied):
            ssh.put_file_by_fl(io.BytesIO(b'a' * 70000), '/tmp/private')
        self.assertEqual(remote.write.call_count, 1)
        self.assertLess(len(remote.write.call_args.args[0]), 70000)
        remote.close.assert_called_once()

    def test_real_ssh_unicode_script_is_split_by_encoded_byte_size(self):
        ssh, sftp, remote = self.guarded_ssh(), MagicMock(), MagicMock()
        sftp.open.return_value = remote
        remote.write.side_effect = lambda data: self.a.roles.clear()
        ssh.sftp = sftp
        with self.assertRaises(access.AccessDenied):
            ssh.put_file_by_fl(io.StringIO('中' * 20000), '/tmp/private')
        self.assertEqual(remote.write.call_count, 1)
        self.assertLessEqual(len(remote.write.call_args.args[0]), 32768)

    def test_real_authorized_upload_preserves_content_and_reports_progress(self):
        ssh, sftp, remote, callback = self.guarded_ssh(), MagicMock(), MagicMock(), MagicMock()
        sftp.open.return_value = remote
        ssh.sftp = sftp
        data = ('中a' * 20000).encode()
        ssh.put_file_by_fl(io.BytesIO(data), '/tmp/private', callback=callback)
        self.assertEqual(b''.join(call.args[0] for call in remote.write.call_args_list), data)
        self.assertEqual(callback.call_args.args[0], len(data))
        remote.close.assert_called_once()

    def test_real_ssh_output_is_not_yielded_after_receive_revokes_permission(self):
        ssh, channel = self.guarded_ssh(), MagicMock()
        ssh.channel = channel
        def receive(_size):
            self.a.roles.clear()
            return b'private output'
        channel.recv.side_effect = receive
        with patch.object(ssh, '_handle_command', return_value='true\n'):
            with self.assertRaises(access.AccessDenied):
                next(ssh.exec_command_with_stream('true'))
        self.assertEqual(channel.recv.call_count, 1)

    def test_real_ssh_raw_client_rechecks_before_shell_open(self):
        ssh, client = self.guarded_ssh(), MagicMock()
        ssh.client = client
        guarded_client = ssh.get_client()
        self.a.roles.clear()
        with self.assertRaises(access.AccessDenied):
            guarded_client.invoke_shell(term='xterm')
        client.invoke_shell.assert_not_called()

    def test_terminal_low_level_output_denial_closes_websocket_and_connection(self):
        from consumer.consumers import SSHConsumer
        from libs.ssh import SSH
        ws, raw = SSHConsumer(), MagicMock()
        ws.user, ws.id, ws.ssh = self.a, self.ha.pk, MagicMock()
        ws.base_send = MagicMock()
        def receive(_size):
            self.a.roles.clear()
            return b'private-output'
        raw.recv.side_effect = receive
        ws.chan = SSH('192.0.2.1', authorize=ws.authorize)._guard(raw)
        ws.loop_read()
        self.assertTrue(ws.closed)
        raw.close.assert_called_once()
        ws.ssh.close.assert_called_once()
        self.assertNotIn('private-output', str(ws.base_send.call_args_list))

    def test_terminal_low_level_shell_open_denial_closes_connection(self):
        from consumer.consumers import SSHConsumer
        from libs.ssh import SSH
        ws, raw, channel = SSHConsumer(), MagicMock(), MagicMock()
        ws.user, ws.base_send = self.a, MagicMock()
        ws.scope = {'url_route': {'kwargs': {'id': self.ha.pk}}}
        def shell(*args, **kwargs):
            self.a.roles.clear()
            return channel
        raw.invoke_shell.side_effect = shell
        ssh = SSH('192.0.2.1', authorize=ws.authorize)
        ssh.client = raw
        with patch.object(Host, 'get_ssh', return_value=ssh), patch('consumer.consumers.Thread') as thread:
            ws.init()
        self.assertTrue(ws.closed)
        raw.close.assert_called_once()
        channel.close.assert_called_once()
        thread.assert_not_called()

    def test_terminal_low_level_input_denial_closes_connection(self):
        from consumer.consumers import SSHConsumer
        from libs.ssh import SSH
        ws, raw = SSHConsumer(), MagicMock()
        ws.user, ws.id, ws.ssh, ws.base_send = self.a, self.ha.pk, MagicMock(), MagicMock()
        raw.send.side_effect = lambda data: self.a.roles.clear()
        ws.chan = SSH('192.0.2.1', authorize=ws.authorize)._guard(raw)
        ws.receive(text_data=json.dumps({'data': 'true\n'}))
        self.assertTrue(ws.closed)
        raw.close.assert_called_once()
        ws.ssh.close.assert_called_once()

    def test_transfer_dispatch_denial_always_cleans_staging(self):
        from apps.exec.transfer import _dispatch_sync
        task = Transfer.objects.create(user=self.a, digest='f' * 32, src_dir='/tmp/staged', dst_dir='/tmp/b',
                                       host_ids=json.dumps([self.ha.pk]))
        self.a.roles.clear()
        with patch('apps.exec.transfer.remove_mount_dir') as cleanup:
            with self.assertRaises(access.AccessDenied):
                _dispatch_sync(task)
        cleanup.assert_called_once_with(task.src_dir, task.host_id)

    def test_transfer_upload_denial_cleans_staging(self):
        with patch('apps.exec.transfer.os.makedirs'), patch('apps.exec.transfer.open') as local_file, \
                patch('apps.exec.transfer.remove_mount_dir') as cleanup:
            local_file.return_value.__enter__.return_value.write.side_effect = lambda chunk: self.a.roles.clear()
            response = self.client.post('/exec/transfer/', {'data': json.dumps({
                'host_ids': [self.ha.pk], 'dst_dir': '/tmp/dst'}), 'file0': io.BytesIO(b'content')},
                HTTP_X_TOKEN=self.a.access_token)
            self.assert_denied(response)
        self.assertEqual(cleanup.call_count, 1)
        self.assertFalse(Transfer.objects.exists())
