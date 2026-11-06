"""The resource permission release is distinct from the old SSL contract."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
MODELS = {
    'account': {'ResourceQuotaLock': 'account_resource_quota_lock',
                'AccountResourcePolicy': 'account_resource_policies',
                'ResourceOwnership': 'account_resource_ownerships',
                'ResourceGrant': 'account_resource_grants',
                'ResourceGroupOwner': 'account_resource_group_owners',
                'ResourceAccessAudit': 'account_resource_audit'},
    'alarm': {'AlarmResource': 'alarm_resource_scopes'},
    'notify': {'NotifyAudience': 'notify_resource_audience'},
    'deploy': {'DeployUpload': 'deploy_resource_uploads'},
}


def load(test, filename):
    path = HERE / filename
    test.assertTrue(path.is_file(), 'Missing independent release contract: ' + filename)
    spec = importlib.util.spec_from_file_location('resource_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrations():
    files = {}
    for app, models in MODELS.items():
        operations = ["migrations.CreateModel(name=%r, fields=[('id',models.BigAutoField(primary_key=True))], options={'db_table':%r})" % (name, table)
                      for name, table in models.items()]
        files['apps/%s/migrations/0002_account_resources.py' % app] = (
            'from django.db import migrations, models\n'
            'class Migration(migrations.Migration):\n'
            ' dependencies=[(%r, "0001_initial")]\n operations=[%s]\n' % (app, ','.join(operations)))
    return files


class FrozenMigrationTests(unittest.TestCase):
    def test_only_nine_named_models_and_fixed_operations_are_accepted(self):
        contract = load(self, 'contract.py')
        facts = contract.validate_migrations(migrations())
        self.assertEqual(set(facts), set(migrations()))
        self.assertEqual(sum(len(f['operations']) for f in facts.values()), 9)
        self.assertTrue(all(len(f['sha256']) == 64 for f in facts.values()))

    def test_existing_schema_runpython_extra_model_and_expression_are_rejected(self):
        contract = load(self, 'contract.py')
        name = 'apps/alarm/migrations/0002_account_resources.py'
        for old, new in (
            ('migrations.CreateModel', 'migrations.AlterModelTable'),
            ("name='AlarmResource'", "name='Alarm'"),
            ("'alarm_resource_scopes'", "'alarms'"),
            ('models.BigAutoField(primary_key=True)', "__import__('os').system('bad')"),
            ('operations=[', 'operations=[migrations.RunPython(print),'),
        ):
            files = migrations()
            self.assertIn(old, files[name])
            files[name] = files[name].replace(old, new)
            with self.subTest(new=new), self.assertRaises(ValueError):
                contract.validate_migrations(files)
        files = migrations()
        files[name] += '\nprint("side effect")\n'
        with self.assertRaises(ValueError):
            contract.validate_migrations(files)

    def test_missing_model_wrong_profile_and_extra_migration_fail_closed(self):
        contract = load(self, 'contract.py')
        files = migrations()
        del files['apps/notify/migrations/0002_account_resources.py']
        with self.assertRaises(ValueError):
            contract.validate_migrations(files)
        files = migrations()
        files['apps/monitor/migrations/0003_ssl_monitor.py'] = 'pass'
        with self.assertRaises(ValueError):
            contract.validate_migrations(files)

    def test_live_baseline_binds_graph_schema_and_application_separately_from_image_tag(self):
        baseline = json.loads(HERE.joinpath('production-baseline.json').read_text())
        self.assertEqual(baseline['previous_revision'], '4739172403439e5e5a0ab37c733509714d4c35ee')
        self.assertEqual(baseline['image_tag'], 'spug-baota:d48d73b')
        self.assertEqual(set(baseline['migration_graph']), set(baseline['applied_migrations']))
        self.assertEqual(len(baseline['migration_graph']), 24)
        self.assertEqual(len(baseline['schema']), 67)

    def test_restart_contract_never_generates_schema_and_rollback_keeps_maintenance_closed(self):
        contract = load(self, 'contract.py')
        candidate, rollback = contract.dockerfiles('a' * 40, ['apps/account/migrations/0002_account_resources.py'],
                                                   {'apps/account/access.py': 'b' * 64})
        self.assertIn('COPY image-source/spug_api/', candidate)
        self.assertIn('migrate --noinput', candidate)
        for image in (candidate, rollback):
            self.assertIn("! grep -Eq 'updatedb|makemigrations'", image)
        self.assertIn('autostart', rollback)
        self.assertIn('false', rollback)
        self.assertNotIn('DROP', rollback)

    def test_both_images_pin_full_existing_runtime_and_audit_transport(self):
        contract = load(self, 'contract.py')
        candidate, rollback = contract.dockerfiles('a' * 40, contract.MIGRATIONS,
            {'apps/account/access.py': 'b' * 64},
            preserved_api_sha256={'apps/baota/operations/certificates.py': 'c' * 64,
                                  'apps/baota/data/official_api_catalog.json': 'd' * 64},
            rollback_api_sha256={'apps/account/access.py': 'e' * 64,
                                 'apps/baota/operations/certificates.py': 'c' * 64},
            history_sha256={'apps/account/migrations/0001_initial.py': 'f' * 64})
        self.assertIn('COPY rollback-image-source/spug_api/', rollback)
        self.assertIn('COPY image-source/spug_api/', candidate)
        self.assertIn('KOPS_RESOURCE_AUDIT_LOG', candidate)
        self.assertIn('/data/logs/resource-access.log', candidate)
        self.assertIn('account-resource-maintenance-migrations-held', rollback)
        for image in (candidate, rollback):
            self.assertLessEqual(image.count('COPY '), 2)

    def test_image_copy_preserves_exact_existing_migration_tool_without_opening_tools(self):
        contract = load(self, 'contract.py')
        arguments = ('a' * 40, contract.MIGRATIONS, {'apps/account/access.py': 'b' * 64})
        contract.dockerfiles(*arguments,
            preserved_api_sha256={'tools/migrate.py': 'c' * 64},
            rollback_api_sha256={'tools/migrate.py': 'c' * 64})
        for name in ('tools/other.py', 'tools/migrate.json', 'tools/../migrate.py', '/tools/migrate.py'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'image-copy-path-invalid'):
                contract.dockerfiles(*arguments, preserved_api_sha256={name: 'c' * 64})


class SchemaPreservationTests(unittest.TestCase):
    def snapshots(self):
        before = {'schema': {'hosts': 'a' * 64},
                  'business': {'hosts': {'count': 2, 'digest': 'b' * 64}},
                  'migrations': {'account/0001_initial': 'c' * 64}}
        after = copy.deepcopy(before)
        for models in MODELS.values():
            for table in models.values():
                after['schema'][table] = 'd' * 64
                after['business'][table] = {'count': 0, 'digest': 'e' * 64}
        for app in MODELS:
            after['migrations'][app + '/0002_account_resources'] = 'f' * 64
        return before, after

    def test_exact_additions_preserve_nonempty_old_rows_and_every_old_schema(self):
        contract = load(self, 'contract.py')
        before, after = self.snapshots()
        contract.verify_additive(before, after, contract.validate_migrations(migrations()))

    def test_old_drift_partial_history_nonempty_additions_and_unrelated_table_fail(self):
        contract = load(self, 'contract.py')
        for mode in ('row', 'schema', 'history', 'nonempty', 'extra'):
            before, after = self.snapshots()
            if mode == 'row': after['business']['hosts']['count'] += 1
            if mode == 'schema': after['schema']['hosts'] = '0' * 64
            if mode == 'history': del after['migrations']['notify/0002_account_resources']
            if mode == 'nonempty': after['business']['account_resource_policies']['count'] = 1
            if mode == 'extra': after['schema']['unreviewed'] = '0' * 64
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                contract.verify_additive(before, after, contract.validate_migrations(migrations()))


if __name__ == '__main__':
    unittest.main()
