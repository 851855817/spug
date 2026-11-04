"""Exact six-table, one-migration publication contract; no reverse DDL."""
import re


SSL_TABLES = (
    'monitor_ssl_sources', 'monitor_ssl_certificates', 'monitor_ssl_jobs',
    'monitor_ssl_leases', 'monitor_ssl_reminders', 'monitor_ssl_reminder_preferences',
)


class SchemaFailure(Exception):
    """Fixed reasons only; never include table contents or credentials."""


def require(value, reason):
    if not value:
        raise SchemaFailure(reason)


def verify_additive(before, after, migration):
    require(isinstance(migration, str) and re.fullmatch(r'[0-9]{4}_[a-z0-9_]+', migration),
            'invalid-migration-name')
    require(set(before) == set(after) == {'business', 'schema', 'migrations'}, 'probe-envelope-invalid')
    expected = set(SSL_TABLES)
    old_schema, new_schema = before['schema'], after['schema']
    require(not (set(old_schema) & expected), 'ssl-schema-already-exists')
    require(set(new_schema) == set(old_schema) | expected, 'schema-additions-not-exact')
    require(all(new_schema[name] == value for name, value in old_schema.items()), 'old-schema-changed')
    old_rows, new_rows = before['business'], after['business']
    require(set(new_rows) == set(old_rows) | expected, 'business-additions-not-exact')
    for name, value in old_rows.items():
        if name not in ('schema_sha256', 'migration_rows_sha256'):
            require(new_rows[name] == value, 'old-business-changed')
    require(all(isinstance(new_rows[name], dict) and new_rows[name].get('count') == 0
                and set(new_rows[name]) == {'count', 'digest'} for name in expected), 'new-tables-not-empty')
    old_migrations, new_migrations = before['migrations'], after['migrations']
    addition = 'monitor/' + migration
    require(addition not in old_migrations and set(new_migrations) == set(old_migrations) | {addition},
            'migration-additions-not-exact')
    require(all(new_migrations[name] == value for name, value in old_migrations.items()),
            'old-migration-history-changed')


def build_probe(legacy, migration):
    """Retain every existing idle/credential guard and append exact DDL facts.

    The existing business schema and migration digests deliberately exclude only
    the reviewed six new tables and single new migration. Full facts are returned
    separately so no unrelated DDL or history change can evade verification.
    """
    require(isinstance(migration, str) and re.fullmatch(r'[0-9]{4}_[a-z0-9_]+', migration),
            'invalid-migration-name')
    substitutions = {
        'for table in sorted(database.introspection.table_names()):':
            'for table in sorted(set(database.introspection.table_names()) - set(' + repr(SSL_TABLES) + ')):',
        "assert table in tables, 'Missing business schema; refuse release'":
            'if table in ' + repr(SSL_TABLES) + ":\n        continue\n    assert table in tables, 'Missing business schema; refuse release'",
        "result['migration_rows_sha256'] = digest(cursor.fetchall())":
            "result['migration_rows_sha256'] = digest([row for row in cursor.fetchall() if row[:2] != ('monitor', " + repr(migration) + ')])',
        'print(json.dumps(result, sort_keys=True))':
            'print(json.dumps(ssl_envelope(result), sort_keys=True))',
    }
    # The output anchor is asserted, never fuzzy-matched against a newer guard.
    if 'print(json.dumps(result, sort_keys=True))' not in legacy:
        substitutions.pop('print(json.dumps(result, sort_keys=True))')
        substitutions['print(json.dumps(result))'] = 'print(json.dumps(ssl_envelope(result), sort_keys=True))'
    for old, new in substitutions.items():
        require(legacy.count(old) == 1, 'legacy-probe-anchor-mismatch')
        legacy = legacy.replace(old, new)
    helper = '''
def ssl_envelope(business):
    full_schema = {}
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            full_schema[table] = digest({
                'columns': connection.introspection.get_table_description(cursor, table),
                'constraints': connection.introspection.get_constraints(cursor, table)})
        cursor.execute('SELECT app, name, applied FROM django_migrations ORDER BY app, name')
        migration_rows = {row[0] + '/' + row[1]: digest(row) for row in cursor.fetchall()}
    for model in apps.get_models():
        table = model._meta.db_table
        if table in SSL_ADDITIONS and table in full_schema:
            rows = list(model.objects.order_by('pk').values())
            business[table] = {'count': len(rows), 'digest': digest(rows)}
    return {'business': business, 'schema': full_schema, 'migrations': migration_rows}

'''
    anchor = 'models = [model for model in apps.get_models(include_auto_created=True)'
    require(legacy.count(anchor) == 1, 'legacy-probe-anchor-mismatch')
    return legacy.replace(anchor, 'SSL_ADDITIONS = ' + repr(SSL_TABLES) + '\n' + helper + anchor)
