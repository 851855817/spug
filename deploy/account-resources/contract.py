"""Independent, non-executable grammar for this exact nine-model release."""
import ast
import hashlib
import json
from pathlib import Path
import re


PROFILE = 'account-resources-v1'
BASELINE = json.loads(Path(__file__).with_name('production-baseline.json').read_text())
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
TABLES = {table for models in MODELS.values() for table in models.values()}
MIGRATIONS = {'apps/%s/migrations/0002_account_resources.py' % app: app for app in MODELS}
FIELDS = {'AutoField', 'BigAutoField', 'PositiveSmallIntegerField', 'PositiveIntegerField',
          'PositiveBigIntegerField', 'CharField', 'BooleanField', 'DateTimeField', 'JSONField',
          'ForeignKey', 'OneToOneField', 'TextField', 'CheckConstraint', 'UniqueConstraint', 'Q'}


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def attribute(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return attribute(node.value) + '.' + node.attr
    raise ValueError('migration-expression-forbidden')


def validate_migrations(files):
    require(isinstance(files, dict) and set(files) == set(MIGRATIONS), 'migration-file-set-not-exact')
    facts = {}
    known = set(BASELINE['migration_graph']) | {
        app + '/0002_account_resources' for app in MODELS}
    for path, source in sorted(files.items()):
        require(isinstance(source, str) and len(source) < 512 * 1024, 'migration-size-invalid')
        try: tree = ast.parse(source)
        except SyntaxError: raise ValueError('migration-syntax-invalid') from None
        classes = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                require(node.module == 'django.db' and not node.level and
                        all(n.name in ('models', 'migrations') and n.asname is None for n in node.names),
                        'migration-import-forbidden')
            elif isinstance(node, ast.Import):
                require(all(n.name == 'django.db.models.deletion' and n.asname is None for n in node.names),
                        'migration-import-forbidden')
            elif isinstance(node, ast.ClassDef): classes.append(node)
            elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, str)):
                raise ValueError('migration-top-level-code-forbidden')
        require(len(classes) == 1, 'migration-class-invalid')
        cls = classes[0]
        require(cls.name == 'Migration' and not cls.decorator_list and not cls.keywords and
                len(cls.bases) == 1 and attribute(cls.bases[0]) == 'migrations.Migration', 'migration-class-invalid')
        assignments = {}
        for node in cls.body:
            require(isinstance(node, ast.Assign) and len(node.targets) == 1 and
                    isinstance(node.targets[0], ast.Name), 'migration-statement-forbidden')
            key = node.targets[0].id
            require(key in ('dependencies', 'operations') and key not in assignments, 'migration-assignment-invalid')
            assignments[key] = node.value
        require(set(assignments) == {'dependencies', 'operations'}, 'migration-sections-invalid')
        try: dependencies = ast.literal_eval(assignments['dependencies'])
        except (TypeError, ValueError): raise ValueError('migration-dependencies-invalid') from None
        app = MIGRATIONS[path]
        require(isinstance(dependencies, list) and dependencies and
                all(isinstance(d, tuple) and len(d) == 2 and all(isinstance(x, str) for x in d)
                    and '/'.join(d) in known and d != (app, '0002_account_resources') for d in dependencies),
                'migration-dependencies-invalid')
        require((app, '0001_initial') in dependencies and len(dependencies) == len(set(dependencies)),
                'migration-baseline-dependency-missing')
        operations = assignments['operations']
        require(isinstance(operations, ast.List) and 1 <= len(operations.elts) <= 40, 'migration-operations-invalid')
        created, summaries = set(), []
        for operation in operations.elts:
            require(isinstance(operation, ast.Call) and not operation.args, 'migration-operation-invalid')
            kind = attribute(operation.func)
            require(kind in ('migrations.CreateModel', 'migrations.AddConstraint'), 'migration-operation-not-additive')
            keywords = {k.arg: k.value for k in operation.keywords}
            require(None not in keywords and len(keywords) == len(operation.keywords), 'migration-keyword-invalid')
            if kind == 'migrations.CreateModel':
                require(set(keywords) == {'name', 'fields', 'options'}, 'migration-create-options-invalid')
                name = ast.literal_eval(keywords['name'])
                require(name in MODELS[app] and name not in created, 'migration-model-not-reviewed')
                opts = keywords['options']
                require(isinstance(opts, ast.Dict), 'migration-model-options-invalid')
                options = {ast.literal_eval(k): v for k, v in zip(opts.keys, opts.values)}
                require(set(options) <= {'db_table', 'constraints'} and 'db_table' in options and
                        ast.literal_eval(options['db_table']) == MODELS[app][name], 'migration-table-not-reviewed')
                require(isinstance(keywords['fields'], ast.List) and keywords['fields'].elts,
                        'migration-fields-empty')
                created.add(name)
            else:
                require(set(keywords) == {'model_name', 'constraint'} and
                        ast.literal_eval(keywords['model_name']) in {n.lower() for n in MODELS[app]},
                        'migration-existing-model-change-forbidden')
            # Restrict every expression, including defaults and constraints.
            for node in ast.walk(operation):
                require(not isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                    ast.GeneratorExp, ast.NamedExpr, ast.Starred, ast.BinOp, ast.Subscript)),
                    'migration-expression-forbidden')
                if isinstance(node, ast.Call) and node is not operation:
                    require(attribute(node.func) in {'models.' + n for n in FIELDS}, 'migration-call-forbidden')
                if isinstance(node, ast.Name):
                    require(node.id in ('models', 'migrations', 'django', 'dict', 'list'), 'migration-name-forbidden')
                if isinstance(node, ast.Attribute):
                    attr = attribute(node)
                    require(attr in {'models.' + n for n in FIELDS} | {
                        'migrations.CreateModel', 'migrations.AddConstraint', 'django.db', 'django.db.models',
                        'django.db.models.deletion', 'django.db.models.deletion.CASCADE',
                        'django.db.models.deletion.PROTECT', 'django.db.models.deletion.SET_NULL'},
                        'migration-attribute-forbidden')
            summaries.append({'operation': kind.split('.')[-1], 'model': name if kind.endswith('CreateModel')
                              else ast.literal_eval(keywords['model_name']),
                              'expression_sha256': sha(ast.dump(operation, include_attributes=False).encode())})
        require(created == set(MODELS[app]), 'migration-model-set-not-exact')
        facts[path] = {'sha256': sha(source.encode()), 'dependencies': dependencies, 'operations': summaries}
    return facts


def verify_additive(before, after, migration_facts):
    require(set(migration_facts) == set(MIGRATIONS), 'migration-proof-not-exact')
    require(set(before) == set(after) == {'schema', 'business', 'migrations'}, 'preservation-envelope-invalid')
    require(not set(before['schema']) & TABLES, 'resource-tables-already-exist')
    require(set(after['schema']) == set(before['schema']) | TABLES and
            all(after['schema'][k] == v for k, v in before['schema'].items()), 'old-schema-changed')
    require(set(after['business']) == set(before['business']) | TABLES and
            all(after['business'][k] == v for k, v in before['business'].items()), 'old-business-changed')
    require(all(set(after['business'][t]) == {'count', 'digest'} and after['business'][t]['count'] == 0
                for t in TABLES), 'new-tables-not-empty')
    additions = {app + '/0002_account_resources' for app in MODELS}
    require(not set(before['migrations']) & additions and
            set(after['migrations']) == set(before['migrations']) | additions and
            all(after['migrations'][k] == v for k, v in before['migrations'].items()), 'migration-history-changed')


def dockerfiles(revision, migration_paths, api_sha256, preserved_api_sha256=None,
                rollback_api_sha256=None, history_sha256=None):
    require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision)
            and revision not in ('0' * 40, BASELINE['previous_revision']), 'revision-invalid')
    require(set(migration_paths) <= set(MIGRATIONS), 'image-migration-path-invalid')
    require(all(re.fullmatch(r'(apps|libs|consumer|spug)/[A-Za-z0-9_./-]+\.(py|json)', p)
                and '..' not in p.split('/') for p in api_sha256), 'image-api-path-invalid')
    header = 'FROM ' + BASELINE['image_tag'] + '\n'
    patch = ("RUN test \"$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)\" -eq 1 && "
             "sed -i 's/^python3 manage.py updatedb$/python3 manage.py migrate --noinput/' /entrypoint.sh && "
             "! grep -Eq 'updatedb|makemigrations' /entrypoint.sh\n")
    paths = set(api_sha256) | set(migration_paths) | set(preserved_api_sha256 or {}) | set(rollback_api_sha256 or {}) | set(history_sha256 or {})
    require(all((p in ('manage.py', 'tools/migrate.py') or re.fullmatch(r'(apps|libs|consumer|spug)/[A-Za-z0-9_./-]+\.(py|json)', p))
                and '..' not in p.split('/') for p in paths), 'image-copy-path-invalid')
    candidate = (header + 'LABEL org.opencontainers.image.revision="' + revision + '"\n' + patch +
                 'ENV KOPS_RESOURCE_AUDIT_LOG=/data/logs/resource-access.log\n' +
                 'COPY image-source/spug_api/ /opt/spug/spug_api/\n' +
                 'COPY build/ /opt/spug/spug_web/build/\n')
    hold = ("RUN test \"$(grep -Fxc 'python3 manage.py updatedb' /entrypoint.sh)\" -eq 1 && "
            "sed -i 's/^python3 manage.py updatedb$/: account-resource-maintenance-migrations-held/' /entrypoint.sh && "
            "! grep -Eq 'updatedb|makemigrations' /entrypoint.sh\n")
    closed = ('RUN python3 -c "import configparser;from pathlib import Path;'
              "p=Path('/etc/supervisord.conf');c=configparser.ConfigParser(interpolation=None);c.read(p);"
              "names=[n for n in c.sections() if n.startswith('program:') and n!='program:redis'];"
              "assert len(names)==8;[(c.set(n,k,'false')) for n in names for k in ('autostart','autorestart')];"
              "f=p.open('w');c.write(f);f.close()\"\n")
    rollback = (header + 'LABEL org.opencontainers.image.revision="' + BASELINE['previous_revision'] + '"\n' +
                hold + 'COPY rollback-image-source/spug_api/ /opt/spug/spug_api/\n' + closed)
    return candidate, rollback
