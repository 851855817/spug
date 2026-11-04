"""Disposable MariaDB exercise used by prepare.py, never by the live app."""
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'spug.baota_ci_settings'
    import django
    django.setup()
    from django.apps import apps
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from schema import SSL_TABLES, verify_additive, SchemaFailure

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def snapshot():
        tables = set(connection.introspection.table_names())
        rows = {}
        for model in apps.get_models(include_auto_created=True):
            if model._meta.db_table in tables:
                values = list(model.objects.order_by('pk').values())
                rows[model._meta.db_table] = {'count': len(values), 'digest': digest(values)}
        with connection.cursor() as cursor:
            full_schema = {name: digest({'columns': connection.introspection.get_table_description(cursor, name),
                'constraints': connection.introspection.get_constraints(cursor, name)}) for name in sorted(tables)}
            cursor.execute('SELECT app, name, applied FROM django_migrations ORDER BY app, name')
            migrations = {row[0] + '/' + row[1]: digest(row) for row in cursor.fetchall()}
        # Migration bookkeeping is checked separately, not as a business model.
        return {'business': rows, 'schema': full_schema, 'migrations': migrations}

    call_command('migrate', interactive=False, verbosity=0)
    state = MigrationExecutor(connection).loader.project_state()
    Setting = state.apps.get_model('setting', 'Setting')
    Setting.objects.create(key='ssl-release-sentinel', value='preserve-existing-nonempty-data')
    User = state.apps.get_model('account', 'User')
    actor = User.objects.create(username='ssl-disposable-fixture', is_supper=True)
    Host = state.apps.get_model('host', 'Host')
    Host.objects.create(name='fixture-host', hostname='192.0.2.5', username='fixture', created_by=actor)
    Group = state.apps.get_model('baota', 'PanelGroup')
    group = Group.objects.create(name='ssl-disposable-group')
    Panel = state.apps.get_model('baota', 'Panel')
    Panel.objects.create(name='fixture-panel', url='http://192.0.2.5:8888', username='fixture',
                         password_ciphertext='disposable-fixture', group=group, created_by=actor)
    before = snapshot()
    call_command('makemigrations', 'monitor', name='ssl_monitor', interactive=False, verbosity=1)
    files = list(Path('apps/monitor/migrations').glob('*_ssl_monitor.py'))
    assert len(files) == 1
    migration = files[0].stem
    call_command('migrate', interactive=False, verbosity=0)
    call_command('makemigrations', check=True, dry_run=True, interactive=False, verbosity=0)
    after = snapshot()
    verify_additive(before, after, migration)
    assert all(not model.objects.exists() for model in apps.get_models() if model._meta.db_table in SSL_TABLES)
    # Fail closed if DDL exists but Django did not record its migration.
    from django.db.migrations.recorder import MigrationRecorder
    recorder = MigrationRecorder(connection)
    recorder.record_unapplied('monitor', migration)
    try:
        try:
            verify_additive(before, snapshot(), migration)
        except SchemaFailure:
            pass
        else:
            raise AssertionError('Unrecorded migration was accepted')
    finally:
        recorder.record_applied('monitor', migration)
    Path('/tmp/ssl-rehearsal.json').write_text(json.dumps({'migration': migration,
        'new_schema': {name: after['schema'][name] for name in SSL_TABLES}, 'append_only': True}))
    print('Append-only SSL migration, nonempty legacy data, six empty tables and partial-history refusal verified.')


if __name__ == '__main__':
    # Application source is the explicit working directory inside a disposable
    # container; this script's directory contains only release utilities.
    sys.path.insert(0, str(Path.cwd()))
    main()
