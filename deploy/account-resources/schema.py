"""Retain the existing idle/credential guard and expose all nine additions."""
import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('resource_probe_contract', Path(__file__).with_name('contract.py'))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def build_probe(legacy):
    tables = tuple(sorted(contract.TABLES))
    nodes = tuple(sorted(app + '/0002_account_resources' for app in contract.MODELS))
    substitutions = {
        'for table in sorted(database.introspection.table_names()):':
            'for table in sorted(set(database.introspection.table_names()) - set(' + repr(tables) + ')):',
        "assert table in tables, 'Missing business schema; refuse release'":
            'if table in ' + repr(tables) + ":\n        continue\n    assert table in tables, 'Missing business schema; refuse release'",
        "result['migration_rows_sha256'] = digest(cursor.fetchall())":
            "result['migration_rows_sha256'] = digest([row for row in cursor.fetchall() if row[0] + '/' + row[1] not in " + repr(nodes) + '])',
    }
    output = 'print(json.dumps(result, sort_keys=True))'
    if output not in legacy: output = 'print(json.dumps(result))'
    substitutions[output] = 'print(json.dumps(resource_envelope(result), sort_keys=True))'
    for old, new in substitutions.items():
        contract.require(legacy.count(old) == 1, 'preservation-probe-anchor-mismatch')
        legacy = legacy.replace(old, new)
    helper = '''
def resource_envelope(business):
    full_schema = {}
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            full_schema[table] = digest({
                'columns': connection.introspection.get_table_description(cursor, table),
                'constraints': connection.introspection.get_constraints(cursor, table)})
        cursor.execute('SELECT app, name, applied FROM django_migrations ORDER BY app, name')
        migrations = {row[0] + '/' + row[1]: digest(row) for row in cursor.fetchall()}
    # Old runtime ignores added tables. Its retained frozen migration history
    # supplies the historical models needed to verify those tables on rollback.
    for model in MigrationExecutor(connection).loader.project_state().apps.get_models():
        table = model._meta.db_table
        if table in RESOURCE_ADDITIONS and table in full_schema:
            rows = list(model.objects.order_by('pk').values())
            business[table] = {'count': len(rows), 'digest': digest(rows)}
    return {'business': business, 'schema': full_schema, 'migrations': migrations}

'''
    anchor = 'models = [model for model in apps.get_models(include_auto_created=True)'
    contract.require(legacy.count(anchor) == 1, 'preservation-probe-anchor-mismatch')
    return legacy.replace(anchor, 'RESOURCE_ADDITIONS = ' + repr(tables) + '\n' + helper + anchor)
