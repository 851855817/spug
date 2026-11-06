"""Legacy policy initialization is explicit, repeatable and contains no secrets."""
import io
import json
from django.core.management import call_command
from django.test import TestCase

from apps.account import access
from apps.account.models import User, AccountResourcePolicy, ResourceGrant, ResourceAccessAudit
from apps.baota.models import Panel, PanelGroup
from apps.baota.resource_monitor.models import ResourceMonitorPreference as Preference
from apps.host.models import Host


class ResourceMigrationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='admin', is_supper=True)
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b', is_active=False)
        group = PanelGroup.objects.create(name='secret group name')
        self.host = Host.objects.create(name='secret host', hostname='192.0.2.1', username='secret user',
                                        pkey='PRIVATE_KEY_SENTINEL', created_by=self.a)
        self.panel = Panel.objects.create(name='secret panel', url='http://192.0.2.1/secret',
                    username='secret user', password_ciphertext='PASSWORD_SENTINEL', group=group, created_by=self.a)
        Preference.objects.create(panel=self.panel, linked_host=self.host)

    def report(self, *args):
        output = io.StringIO()
        call_command('resource_permissions', *args, stdout=output)
        raw = output.getvalue()
        for secret in ('PRIVATE_KEY_SENTINEL', 'PASSWORD_SENTINEL', 'secret user', 'secret host', '192.0.2.1'):
            self.assertNotIn(secret, raw)
        return json.loads(raw)

    def test_default_command_only_reports_and_linked_pair_counts_once(self):
        from apps.schedule.models import Task
        Task.objects.create(name='private task', type='host', targets=json.dumps([self.host.pk]),
                            command='SECRET_COMMAND', trigger='date', trigger_args='2027-01-01', created_by=self.b)
        report = self.report()
        row = next(x for x in report['accounts'] if x['id'] == self.a.pk)
        self.assertEqual(row['used'], 1)
        self.assertFalse(AccountResourcePolicy.objects.exists())
        self.assertFalse(ResourceGrant.objects.exists())
        self.assertFalse(ResourceAccessAudit.objects.exists())
        self.assertEqual(report['schedules'][0]['out_of_scope_ids'], [self.host.pk])
        self.assertEqual(report['mode'], 'report')

    def test_explicit_apply_preserves_creator_and_never_auto_shares(self):
        self.report('--apply', '--actor-id', str(self.admin.pk))
        self.assertEqual(AccountResourcePolicy.objects.get(user=self.a).server_limit, 1)
        self.assertEqual(AccountResourcePolicy.objects.get(user=self.b).server_limit, 0)
        self.assertFalse(ResourceGrant.objects.exists())
        self.host.refresh_from_db()
        self.panel.refresh_from_db()
        self.assertEqual((self.host.created_by_id, self.panel.created_by_id), (self.a.pk, self.a.pk))

    def test_repeated_apply_keeps_explicit_policy_and_new_account_default_zero(self):
        access.set_policy(self.admin, self.a, 7)
        self.report('--apply', '--actor-id', str(self.admin.pk))
        self.assertEqual(AccountResourcePolicy.objects.get(user=self.a).server_limit, 7)
        new_user = User.objects.create(username='new')
        self.assertEqual(access.quota_usage(new_user)['limit'], 0)

    def test_non_admin_cannot_apply(self):
        with self.assertRaises(ValueError):
            self.report('--apply', '--actor-id', str(self.a.pk))
        self.assertFalse(AccountResourcePolicy.objects.exists())

    def test_apply_rechecks_administrator_after_waiting_for_graph_lock(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from apps.account.resource_migration import initialize_policies
        @contextmanager
        def revoke_before_lock_returns():
            User.objects.filter(pk=self.admin.pk).update(is_active=False)
            yield
        with patch.object(access, 'quota_transaction', side_effect=revoke_before_lock_returns):
            with self.assertRaises(ValueError):
                initialize_policies(self.admin)
        self.assertFalse(AccountResourcePolicy.objects.exists())

    def test_schema_changes_only_create_the_nine_approved_tables(self):
        from django.apps import apps
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.graph import MigrationGraph
        from django.db.migrations.state import ProjectState
        from django.db.migrations.operations import CreateModel, AddConstraint
        from django.db.migrations import Migration
        from apps.account.resource_migration import ADDITIVE_MODELS
        current = ProjectState.from_apps(apps)
        legacy = current.clone()
        for app, model in ADDITIVE_MODELS:
            legacy.remove_model(app, model)
        graph = MigrationGraph()
        for label in {key[0] for key in legacy.models}:
            graph.add_node((label, '0001_legacy'), Migration('0001_legacy', label))
        changes = MigrationAutodetector(legacy, current).changes(graph=graph)
        created = set()
        for app, migrations in changes.items():
            for migration in migrations:
                for operation in migration.operations:
                    self.assertIsInstance(operation, (CreateModel, AddConstraint))
                    target = operation.name if isinstance(operation, CreateModel) else operation.model_name
                    self.assertIn((app, target.lower()), ADDITIVE_MODELS)
                    if isinstance(operation, CreateModel):
                        created.add((app, target.lower()))
        self.assertEqual(created, set(ADDITIVE_MODELS))
