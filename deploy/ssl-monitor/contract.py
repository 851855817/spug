"""Deterministic prepared files and a non-executable additive migration grammar."""
import ast
import json
import re

from manifest import API_FILES, COMPAT_FILES


MODEL_NAMES = {'SSLSource', 'SSLCertificate', 'SSLJob', 'SSLLease', 'SSLReminder', 'SSLReminderPreference'}
HELPER_SOURCES = {
    'apply-expiry.py': 'source/deploy/ssl-monitor/overlay.py',
    'manifest.py': 'source/deploy/ssl-monitor/manifest.py',
    'deploy-compact-ui.py': 'source/deploy/upgrade-review/publish-ui.py',
    'fingerprint.py': 'source/deploy/upgrade-review/fingerprint.py',
    'release_safety.py': 'source/deploy/inventory/release_safety.py',
}


def require(value, reason):
    if not value: raise ValueError(reason)


def migration_path(name):
    require(isinstance(name, str) and re.fullmatch(r'[0-9]{4}_ssl_monitor', name), 'migration-name-invalid')
    return 'apps/monitor/migrations/' + name + '.py'


def dockerfiles(previous, migration):
    require(isinstance(previous, str) and re.fullmatch('[0-9a-f]{40}', previous), 'previous-revision-invalid')
    path = migration_path(migration)
    header = 'FROM spug-baota:' + previous[:7] + '\nARG SOURCE_REVISION\nLABEL org.opencontainers.image.revision="${SOURCE_REVISION}"\n'
    def copies(names):
        return ''.join('COPY source/spug_api/' + name + ' /opt/spug/spug_api/' + name + '\n' for name in names)
    patch = "RUN test \"$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)\" -eq 1 && sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh && ! grep -Eq 'updatedb|makemigrations' /entrypoint.sh\n"
    return (header + patch + copies([*API_FILES, path]) + 'COPY build/ /opt/spug/spug_web/build/\n',
            header + patch + copies([*COMPAT_FILES, path]))


def attribute(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return attribute(node.value) + '.' + node.attr
    raise ValueError('migration-expression-invalid')


def validate_migration(content):
    require(isinstance(content, str) and len(content) <= 128 * 1024, 'migration-size-invalid')
    try: tree = ast.parse(content)
    except SyntaxError: raise ValueError('migration-syntax-invalid') from None
    imports, classes = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)): imports.append(node)
        elif isinstance(node, ast.ClassDef): classes.append(node)
        elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            raise ValueError('migration-top-level-code-forbidden')
    require(len(classes) == 1 and classes[0].name == 'Migration' and not classes[0].decorator_list and not classes[0].keywords
            and len(classes[0].bases) == 1 and attribute(classes[0].bases[0]) == 'migrations.Migration', 'migration-class-invalid')
    for node in imports:
        if isinstance(node, ast.ImportFrom):
            require(node.module == 'django.db' and not node.level and
                    {name.name for name in node.names} <= {'migrations', 'models'} and
                    all(name.asname is None for name in node.names), 'migration-import-invalid')
        else:
            require(all(name.name in ('django.db.models.deletion', 'apps.monitor.ssl.models')
                        and name.asname is None for name in node.names), 'migration-import-invalid')
    assignments = {}
    for node in classes[0].body:
        require(isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name), 'migration-class-statement-invalid')
        name = node.targets[0].id
        require(name in ('dependencies', 'operations') and name not in assignments, 'migration-assignment-invalid')
        assignments[name] = node.value
    require(set(assignments) == {'dependencies', 'operations'}, 'migration-sections-invalid')
    try: dependencies = ast.literal_eval(assignments['dependencies'])
    except (ValueError, TypeError): raise ValueError('migration-dependencies-invalid') from None
    require(isinstance(dependencies, list) and all(isinstance(row, tuple) and len(row) == 2
            and row[0] in ('monitor', 'account', 'baota') and isinstance(row[1], str)
            and (row[1] == '__first__' or re.fullmatch(r'[0-9]{4}_[a-z0-9_]+', row[1])) for row in dependencies), 'migration-dependencies-invalid')
    operations = assignments['operations']
    require(isinstance(operations, ast.List) and 6 <= len(operations.elts) <= 30, 'migration-operations-invalid')
    created = set()
    allowed_fields = {'AutoField', 'BigAutoField', 'CharField', 'BooleanField', 'DateTimeField',
                      'PositiveSmallIntegerField', 'PositiveIntegerField', 'JSONField', 'ForeignKey', 'UniqueConstraint'}
    for operation in operations.elts:
        require(isinstance(operation, ast.Call) and not operation.args, 'migration-operation-invalid')
        kind = attribute(operation.func)
        require(kind in ('migrations.CreateModel', 'migrations.AddField', 'migrations.AddConstraint'),
                'migration-operation-not-additive')
        keywords = {item.arg: item.value for item in operation.keywords}
        require(len(keywords) == len(operation.keywords) and None not in keywords, 'migration-keywords-invalid')
        if kind == 'migrations.CreateModel':
            require(set(keywords) <= {'name', 'fields', 'options'}, 'migration-create-options-invalid')
            name = ast.literal_eval(keywords.get('name'))
            require(name in MODEL_NAMES and name not in created, 'migration-create-model-invalid')
            created.add(name)
        else:
            require(ast.literal_eval(keywords.get('model_name')) in {name.lower() for name in MODEL_NAMES},
                    'migration-existing-model-alteration-forbidden')
        for node in ast.walk(operation):
            require(not isinstance(node, (ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
                                          ast.NamedExpr, ast.Starred, ast.BinOp, ast.Subscript)), 'migration-expression-invalid')
            if isinstance(node, ast.Call) and node is not operation:
                require(attribute(node.func) in {'models.' + name for name in allowed_fields}, 'migration-call-invalid')
            if isinstance(node, ast.Attribute):
                path = attribute(node)
                require(path.startswith(('models.', 'migrations.', 'django.db.models.deletion.'))
                        or path in ('django.db', 'django.db.models', 'django.db.models.deletion',
                                    'apps.monitor', 'apps.monitor.ssl', 'apps.monitor.ssl.models',
                                    'apps.monitor.ssl.models.revision'), 'migration-attribute-invalid')
            if isinstance(node, ast.Name):
                require(node.id in ('models', 'migrations', 'django', 'apps', 'dict', 'list'), 'migration-name-reference-invalid')
    require(created == MODEL_NAMES, 'migration-model-set-invalid')
    return dependencies


def derived_release(original, migration, digest):
    path = migration_path(migration)
    require(set(original['api_sha256']) == set(API_FILES), 'original-api-set-invalid')
    return dict(original, migration=migration,
                api_sha256=dict(original['api_sha256'], **{path: digest}),
                previous_api_sha256=dict(original['previous_api_sha256'], **{path: None}))
