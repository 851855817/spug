import json
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from apps.account.models import User, Role
from apps.host.models import Host
from apps.app.models import App, Deploy, DeployExtend2
from apps.config.models import Environment
from apps.deploy.models import DeployRequest
from apps.deploy.views import RequestDetailView, RequestView
from apps.home.views import get_deploy


class DeployResourceTests(TestCase):
    def setUp(self):
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b')
        self.host = Host.objects.create(name='own', hostname='192.0.2.1', username='root', pkey='fixture', created_by=self.a)
        self.other = Host.objects.create(name='other', hostname='192.0.2.2', username='root', pkey='fixture', created_by=self.b)
        self.app = App.objects.create(name='app', key='app', created_by=self.a)
        self.env = Environment.objects.create(name='env', key='env', created_by=self.a)
        self.role = Role.objects.create(name='release', page_perms={'dashboard': {'dashboard': ['view']},
            'deploy': {'request': ['view', 'do', 'del']}}, deploy_perms={'apps': [self.app.pk], 'envs': [self.env.pk]})
        self.a.roles.add(self.role)
        self.factory = RequestFactory()

    def deploy(self, owner, hosts):
        deploy = Deploy.objects.create(app=self.app, env=self.env, host_ids=json.dumps([h.pk for h in hosts]),
            extend='2', is_audit=False, rst_notify='{}', created_by=owner)
        DeployExtend2.objects.create(deploy=deploy, server_actions='[]', host_actions='[]')
        return deploy

    def test_deploy_detail_cannot_read_another_owners_mixed_result(self):
        deploy = self.deploy(self.b, [self.other])
        req = DeployRequest.objects.create(deploy=deploy, name='secret', extra='[]',
            host_ids=deploy.host_ids, status='3', created_by=self.b)
        request = self.factory.get('/')
        request.user = self.a
        with patch('apps.deploy.views.Helper.fill_outputs', return_value=0) as output:
            response = RequestDetailView.as_view()(request, r_id=req.pk)
        self.assertTrue(json.loads(response.content)['error'])
        output.assert_not_called()

    def test_home_deployment_counts_use_only_visible_servers(self):
        self.deploy(self.a, [self.host])
        self.deploy(self.b, [self.other])
        request = self.factory.get('/')
        request.user = self.a
        data = json.loads(get_deploy(request).content)['data']
        self.assertEqual(data['host'], 1)
        self.assertEqual(data['res'][0]['count'], 1)

    def test_dispatch_rechecks_actual_executor_before_creating_output(self):
        from apps.deploy.utils import dispatch
        from apps.account.access import AccessDenied
        deploy = self.deploy(self.a, [self.host])
        req = DeployRequest.objects.create(deploy=deploy, name='run', extra='[]',
            host_ids=deploy.host_ids, status='2', created_by=self.a, do_by=self.a)
        self.a.roles.clear()
        with patch('apps.deploy.utils.get_redis_connection') as redis, \
                patch('apps.deploy.utils.Helper.make'), patch('apps.deploy.utils.ext2_deploy'):
            with self.assertRaises(AccessDenied):
                dispatch(req, [self.host.pk], False)
        redis.assert_not_called()
        req.refresh_from_db()
        self.assertEqual(req.status, '-3')
        self.assertTrue(req.reason)

    def test_dispatch_rejects_targets_not_in_saved_request(self):
        from apps.deploy.utils import dispatch
        deploy = self.deploy(self.a, [self.host])
        req = DeployRequest.objects.create(deploy=deploy, name='run', extra='[]',
            host_ids=deploy.host_ids, status='2', created_by=self.a, do_by=self.a)
        with patch('apps.deploy.utils.get_redis_connection') as redis, \
                patch('apps.deploy.utils.Helper.make'), patch('apps.deploy.utils.ext2_deploy'):
            with self.assertRaises(ValueError):
                dispatch(req, [self.other.pk], False)
        redis.assert_not_called()

    def test_local_release_hooks_do_not_give_normal_user_platform_shell(self):
        from apps.deploy.resources import authorize
        from apps.account.access import AccessDenied
        deploy = self.deploy(self.a, [self.host])
        extend = deploy.extend_obj
        extend.server_actions = '[{"title":"hook","data":"true"}]'
        extend.save()
        with self.assertRaises(AccessDenied):
            authorize(self.a, deploy, manage=True)

    def test_normal_user_cannot_borrow_global_git_key(self):
        from apps.app.utils import fetch_versions
        from apps.account.access import AccessDenied
        from apps.app.models import DeployExtend1
        deploy = Deploy.objects.create(app=self.app, env=self.env, host_ids=json.dumps([self.host.pk]),
            extend='1', is_audit=False, rst_notify='{}', created_by=self.a)
        DeployExtend1.objects.create(deploy=deploy, git_repo='git@example.invalid:repo',
            dst_dir='/srv/app', dst_repo='/srv/releases', versions=3,
            filter_rule='{"data":"","type":"exclude"}')
        with patch('apps.app.utils.Git') as git:
            with self.assertRaises(AccessDenied):
                fetch_versions(deploy, user=self.a)
        git.assert_not_called()

    def test_build_worker_checks_owner_before_fetching_repository(self):
        from apps.repository.utils import dispatch
        from apps.repository.models import Repository
        from apps.account.access import AccessDenied
        deploy = self.deploy(self.a, [self.host])
        rep = Repository.objects.create(app=self.app, env=self.env, deploy=deploy, extra='[]',
            version='fixture', spug_version='fixture', created_by=self.a)
        self.a.roles.clear()
        with patch('apps.repository.utils.get_redis_connection') as redis, \
                patch('apps.repository.utils.Helper.make'), patch('apps.repository.utils._build'):
            with self.assertRaises(AccessDenied):
                dispatch(rep)
        redis.assert_not_called()
        rep.refresh_from_db()
        self.assertEqual(rep.status, '2')

    def test_upload_reference_cannot_be_reused_by_another_account(self):
        from apps.deploy.resources import require_upload
        from apps.account.access import AccessDenied
        from apps.deploy.models import DeployUpload
        deploy = self.deploy(self.a, [self.host])
        DeployUpload.objects.create(owner=self.b, deploy=deploy, path='a' * 32, name='fixture.txt')
        with self.assertRaises(AccessDenied):
            require_upload(self.a, deploy.pk, {'path': 'a' * 32, 'name': 'fixture.txt'})

    def test_application_edit_requires_assigned_application_and_all_servers(self):
        from apps.app.views import AppView
        other_app = App.objects.create(name='other', key='other', created_by=self.b)
        self.a.roles.add(Role.objects.create(name='app-editor', page_perms={'deploy': {'app': ['edit']}}))
        request = self.factory.post('/', json.dumps({'id': other_app.pk, 'name': 'changed', 'key': 'other'}),
            content_type='application/json')
        request.user = self.a
        response = AppView.as_view()(request)
        self.assertTrue(json.loads(response.content)['error'])
        other_app.refresh_from_db()
        self.assertEqual(other_app.name, 'other')

    def test_count_cleanup_requires_manage_for_every_record(self):
        from apps.account import access
        admin = User.objects.create(username='admin', is_supper=True)
        access.set_policy(admin, self.a, 3)
        access.assign(admin, self.a, 'host', [self.other.pk], 'read')
        deploy = self.deploy(self.a, [self.other])
        for name in ('one', 'two'):
            DeployRequest.objects.create(deploy=deploy, name=name, extra='[]', host_ids=deploy.host_ids,
                                         status='3', created_by=self.a)
        request = self.factory.delete('/?mode=count&value=1')
        request.user = self.a
        with patch.object(DeployRequest, 'delete') as delete:
            response = RequestView.as_view()(request)
        self.assertTrue(json.loads(response.content)['error'])
        delete.assert_not_called()

    def test_application_add_does_not_allow_editing_existing_application(self):
        from apps.app.views import AppView
        self.role.page_perms = {'deploy': {'app': ['add']}}
        self.role.save()
        request = self.factory.post('/', json.dumps({'id': self.app.pk, 'name': 'changed', 'key': self.app.key}),
                                    content_type='application/json')
        request.user = self.a
        response = AppView.as_view()(request)
        self.assertTrue(json.loads(response.content)['error'])
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, 'app')

    def test_queued_deploy_notification_rechecks_current_execution_permission(self):
        from apps.deploy.helper import Helper
        deploy = self.deploy(self.a, [self.host])
        deploy.rst_notify = json.dumps({'mode': '2', 'value': 'https://example.com/hook'})
        deploy.save()
        req = DeployRequest.objects.create(deploy=deploy, name='result', extra='[]', host_ids=deploy.host_ids,
                status='3', created_by=self.a, do_by=self.a)
        self.role.page_perms = {}
        self.role.save()
        with patch('apps.deploy.helper.Notification.handle_request') as send:
            Helper.send_deploy_notify(req)
        send.assert_not_called()

    def test_approval_notification_uses_actual_approver(self):
        from apps.deploy.helper import Helper
        approver = User.objects.create(username='admin-approver', is_supper=True)
        deploy = self.deploy(self.a, [self.host])
        deploy.rst_notify = json.dumps({'mode': '2', 'value': 'https://example.com/hook'})
        deploy.save()
        req = DeployRequest.objects.create(deploy=deploy, name='approval', extra='[]', host_ids=deploy.host_ids,
                status='1', created_by=self.a, approve_by=approver)
        self.a.is_active = False
        self.a.save()
        with patch('apps.deploy.helper.Notification.handle_request') as send:
            Helper.send_deploy_notify(req, 'approve_rst')
        send.assert_called_once()

    def test_rollback_approval_notice_uses_rollback_permission(self):
        from apps.app.models import DeployExtend1
        from apps.deploy.views import post_request_ext1_rollback
        deploy = Deploy.objects.create(app=self.app, env=self.env, host_ids=json.dumps([self.host.pk]),
            extend='1', is_audit=True, rst_notify=json.dumps({'mode': '2', 'value': 'https://example.com/hook'}),
            created_by=self.a)
        DeployExtend1.objects.create(deploy=deploy, git_repo='https://example.com/repo',
            dst_dir='/srv/app', dst_repo='/srv/releases', versions=3,
            filter_rule='{"data":"","type":"exclude"}')
        req = DeployRequest.objects.create(deploy=deploy, name='release', extra='[]',
            host_ids=deploy.host_ids, status='3', created_by=self.a, spug_version='fixture')
        request = self.factory.post('/', json.dumps({'request_id': req.pk, 'name': 'rollback',
            'host_ids': [self.host.pk]}), content_type='application/json')
        request.user = self.a
        with patch('apps.deploy.views.Thread') as thread, \
                patch('apps.deploy.helper.Notification.handle_request') as send:
            response = post_request_ext1_rollback(request)
            self.assertIsNone(json.loads(response.content)['error'])
            thread.call_args.kwargs['target'](*thread.call_args.kwargs['args'])
        send.assert_called_once()
