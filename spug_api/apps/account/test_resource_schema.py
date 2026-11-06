"""MariaDB rehearsal of the exact additive DDL, with legacy rows retained."""
from unittest import skipUnless

from django.apps import apps
from django.db import connection
from django.db.migrations.state import ProjectState
from django.test import TransactionTestCase

from apps.account.models import User
from apps.account.resource_migration import ADDITIVE_MODELS
from apps.host.models import Host


@skipUnless(connection.vendor == 'mysql', 'MariaDB additive DDL acceptance only')
class ResourceAdditiveSchemaTests(TransactionTestCase):
    def test_nine_table_rehearsal_preserves_legacy_schema_rows_and_old_orm(self):
        user = User.objects.create(username='legacy')
        host = Host.objects.create(name='legacy', hostname='192.0.2.1', username='root', created_by=user)
        models = [apps.get_model(app, name) for app, name in ADDITIVE_MODELS]
        additions = {model._meta.db_table for model in models}
        legacy_state = ProjectState.from_apps(apps)
        for app, name in ADDITIVE_MODELS:
            legacy_state.remove_model(app, name)
        def old_schema():
            with connection.cursor() as cursor:
                return {table: (connection.introspection.get_table_description(cursor, table),
                                connection.introspection.get_constraints(cursor, table))
                        for table in connection.introspection.table_names() if table not in additions}
        baseline, tables = old_schema(), set(connection.introspection.table_names())
        try:
            with connection.schema_editor() as editor:
                for model in reversed(models):
                    editor.delete_model(model)
            self.assertEqual(set(connection.introspection.table_names()), tables - additions)
            self.assertEqual(old_schema(), baseline)
        finally:
            with connection.schema_editor() as editor:
                for model in models:
                    if model._meta.db_table not in connection.introspection.table_names():
                        editor.create_model(model)
        self.assertEqual(set(connection.introspection.table_names()), tables)
        self.assertEqual(old_schema(), baseline)
        self.assertEqual(legacy_state.apps.get_model('host', 'Host').objects.get(pk=host.pk).name, 'legacy')
        self.assertEqual(Host.objects.get(pk=host.pk).created_by_id, user.pk)
