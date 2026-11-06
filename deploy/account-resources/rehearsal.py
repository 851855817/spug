"""Isolated database rehearsal; rejects every non-disposable configuration."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    assert os.environ.get('BAOTA_CI_DB_HOST') == 'baota-ci-db'
    os.environ['DJANGO_SETTINGS_MODULE'] = 'spug.baota_ci_settings'
    sys.path.insert(0, str(Path.cwd()))
    import django
    django.setup()
    from django.apps import apps
    from django.conf import settings
    from django.db import connection
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder
    from django.db.migrations.state import ProjectState
    from apps.account.resource_migration import impact_report, initialize_policies
    assert connection.vendor == 'mysql'
    assert settings.DATABASES['default']['HOST'] == 'baota-ci-db'
    assert settings.DATABASES['default']['NAME'] == 'baota_ci'
    utilities = Path('/utilities')
    spec = importlib.util.spec_from_file_location('resource_contract_rehearsed', utilities / 'contract.py')
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    schema_spec = importlib.util.spec_from_file_location('resource_schema_rehearsed', utilities / 'schema.py')
    schema_module = importlib.util.module_from_spec(schema_spec)
    schema_spec.loader.exec_module(schema_module)
    manifest_path = Path('/prepared/migration-manifest.json')
    manifest = json.loads(manifest_path.read_text())
    facts = contract.validate_migrations({name: Path(name).read_text() for name in manifest['migrations']})
    # JSON turns tuple dependencies into arrays; compare the serialized facts.
    assert json.loads(json.dumps(facts)) == manifest['migrations']
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
    def snapshot():
        tables = set(connection.introspection.table_names())
        business = {}
        for model in apps.get_models(include_auto_created=True):
            if model._meta.db_table in tables:
                values = list(model.objects.order_by('pk').values())
                business[model._meta.db_table] = {'count': len(values), 'digest': digest(values)}
        with connection.cursor() as cursor:
            schema = {}
            for table in sorted(tables):
                metadata = {'columns': connection.introspection.get_table_description(cursor, table),
                            'constraints': connection.introspection.get_constraints(cursor, table)}
                schema[table] = digest(schema_module.addition_schema(**metadata) if table in contract.TABLES else metadata)
            cursor.execute('SELECT app,name,applied FROM django_migrations ORDER BY app,name')
            migrations = {row[0] + '/' + row[1]: digest(row) for row in cursor.fetchall()}
        return {'schema': schema, 'business': business, 'migrations': migrations}
    graph = contract.BASELINE['migration_graph']
    parents = {parent for proof in graph.values() for parent in proof['parents']}
    baseline_targets = [tuple(key.split('/')) for key in graph if key not in parents]
    executor = MigrationExecutor(connection)
    executor.migrate(baseline_targets)
    old_state = executor.loader.project_state(baseline_targets)
    OldUser = old_state.apps.get_model('account', 'User')
    OldHost = old_state.apps.get_model('host', 'Host')
    OldPanel = old_state.apps.get_model('baota', 'Panel')
    OldGroup = old_state.apps.get_model('baota', 'PanelGroup')
    admin = OldUser.objects.create(username='resource-fixture-admin', is_supper=True)
    regular = OldUser.objects.create(username='resource-fixture-regular')
    inactive = OldUser.objects.create(username='resource-fixture-inactive', is_active=False)
    existing = OldUser.objects.create(username='resource-fixture-explicit')
    host = OldHost.objects.create(name='fixture-host', hostname='192.0.2.11', username='fixture', created_by=regular)
    OldHost.objects.create(name='fixture-inactive-host', hostname='192.0.2.12', username='fixture', created_by=inactive)
    group = OldGroup.objects.create(name='resource-fixture-group')
    panel = OldPanel.objects.create(name='fixture-panel', url='http://192.0.2.11:8888', username='fixture',
                                   password_ciphertext='fixture-not-a-secret', group=group, created_by=regular)
    Pref = old_state.apps.get_model('baota', 'ResourceMonitorPreference')
    Pref.objects.create(panel=panel, linked_host_id=host.pk)
    before = snapshot()
    assert set(before['migrations']) == set(graph)
    assert not set(before['schema']) & contract.TABLES
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert {(m.app_label, m.name) for m, backwards in plan if not backwards} == {
        (app, '0002_account_resources') for app in contract.MODELS}
    assert not any(backwards for _, backwards in plan)
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor = MigrationExecutor(connection)
    assert not MigrationAutodetector(executor.loader.project_state(), ProjectState.from_apps(apps)).changes(graph=executor.loader.graph)
    after = snapshot()
    contract.verify_additive(before, after, facts)
    partial = json.loads(json.dumps(after))
    del partial['migrations']['notify/0002_account_resources']
    try: contract.verify_additive(before, partial, facts)
    except ValueError: pass
    else: raise AssertionError('partial migration history accepted')
    report_before = snapshot()
    impact_report()
    assert snapshot() == report_before, 'read-only impact report mutated database'
    Policy = apps.get_model('account', 'AccountResourcePolicy')
    Policy.objects.create(user_id=existing.pk, server_limit=7)
    created = initialize_policies(admin.pk)
    assert set(created) == {regular.pk, inactive.pk}
    assert Policy.objects.get(user_id=regular.pk).server_limit == 1
    assert Policy.objects.get(user_id=inactive.pk).server_limit == 1
    assert Policy.objects.get(user_id=existing.pk).server_limit == 7
    assert not Policy.objects.filter(user_id=admin.pk).exists()
    stable = snapshot()
    assert initialize_policies(admin.pk) == []
    assert snapshot() == stable, 'repeat initialization changed rows'
    assert all(stable['business'][name] == value for name, value in before['business'].items())
    assert all(stable['schema'][name] == value for name, value in before['schema'].items())
    assert stable['schema'] == after['schema'], 'initialization changed structural schema metadata'
    assert all(stable['migrations'][name] == value for name, value in before['migrations'].items())
    assert OldHost.objects.get(pk=host.pk).created_by_id == regular.pk
    assert OldPanel.objects.get(pk=panel.pk).created_by_id == regular.pk
    proof = {'profile': contract.PROFILE, 'provisional': manifest['provisional'],
             'revision': manifest['revision'], 'migration_manifest_sha256': contract.sha(manifest_path.read_bytes()),
             'database_vendor': connection.vendor, 'old_tables': len(before['schema']),
             'new_tables': len(after['schema']) - len(before['schema']), 'old_migrations': len(graph),
             'new_migrations': len(after['migrations']) - len(before['migrations']),
             'baseline_sha256': digest(before), 'upgraded_sha256': digest(stable),
             'new_schema': {name: after['schema'][name] for name in sorted(contract.TABLES)},
             'read_only_impact': True, 'idempotent_explicit_initialization': True,
             'nonempty_legacy_preserved': True, 'historical_orm_reads': True,
             'partial_history_refused': True}
    proof['initialization_schema_stable'] = True
    Path('/tmp/resource-rehearsal.json').write_text(json.dumps(proof, indent=2, sort_keys=True))
    print(json.dumps(proof, sort_keys=True))


if __name__ == '__main__':
    main()
