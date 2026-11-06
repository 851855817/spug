"""Production adapter for the independent account-resource contract.

Importing is read-only. Only the explicit CLI invokes publication; deployment is
owned by the root task. The legacy SSL profile and its controller are untouched.
"""
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys


HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('resource_publication_base', HERE.parent / 'upgrade-review/publish.py')
contract = load('resource_host_contract', HERE / 'contract.py')
schema = load('resource_host_schema', HERE / 'schema.py')
controller = load('resource_publication_controller', HERE / 'publish.py')
require = base.require
SERVICES = ('nginx', 'spug-api', 'spug-ws', 'spug-worker', 'spug-monitor',
            'spug-panel-worker', 'spug-ops-worker', 'spug-scheduler')
BACKEND = tuple(name for name in SERVICES if name != 'nginx')
AUDIT_PATH = '/data/logs/resource-access.log'


def audit_rotation(mounts, template):
    data = [value for value in mounts if value.get('Destination') == '/data']
    require(len(data) == 1 and data[0].get('Type') == 'bind' and data[0].get('RW') is True,
            'resource-persistent-data-bind-invalid')
    source = data[0].get('Source', '')
    require(re.fullmatch(r'/[A-Za-z0-9_./-]+', source) and '..' not in PurePosixPath(source).parts
            and str(PurePosixPath(source)) == source and source != '/', 'resource-data-bind-path-invalid')
    require(template.count(AUDIT_PATH) == 1, 'resource-rotation-template-invalid')
    return template.replace(AUDIT_PATH, source + '/logs/resource-access.log')


def verify_live_history(files):
    expected = {'apps/' + node.split('/')[0] + '/migrations/' + node.split('/')[1] + '.py': proof['sha256']
                for node, proof in contract.BASELINE['migration_graph'].items()}
    require(len(files['api']) == 2 and all(all(root.get(name) == value for name, value in expected.items())
                                        for root in files['api']), 'resource-live-migration-source-drift')


def next_compose(text, revision, rollback=False):
    tag = contract.BASELINE['image_tag']
    target = 'spug-baota:' + ('resource-rollback-' if rollback else '') + revision[:7]
    pattern = re.compile(r'(?m)^([ \t]*image:[ \t]*)' + re.escape(tag) + r'([ \t]*(?:#[^\r\n]*)?)(\r?)$')
    require(base.valid_revision(revision) and len(list(pattern.finditer(text))) == 1 and text.count(tag) == 1,
            'resource-compose-baseline-image-mismatch')
    return pattern.sub(lambda m: m[1] + target + m[2] + m[3], text)


class ResourceHost(base.DockerHost):
    def __init__(self, revision, actor_id, root=Path('/opt/spug'), scope_sha256=None):
        super().__init__(revision, root)
        require(type(actor_id) is int and actor_id > 0, 'resource-initialization-actor-invalid')
        self.actor_id = actor_id
        self.stage = root / ('account-resources-' + revision[:7])
        self.api_attempted = self.ui_attempted = self.initialization_attempted = False
        self.expected_accounts = None
        self.added = None
        self.supervisor_sha = self.entrypoint_sha = None
        self.record_number = 0
        self.scope_sha256 = scope_sha256
        self.rotation_path = Path('/etc/logrotate.d/kops-resource-audit')
        self.rotation_installed = self.rotation_backed_up = False

    def rotation_state(self):
        path = self.rotation_path
        require(not any(value.is_symlink() for value in (path, *path.parents)), 'resource-rotation-linked')
        if not path.exists(): return {'present': False}
        require(path.is_file(), 'resource-rotation-not-file')
        facts = path.stat()
        return {'present': True, 'sha256': base.digest(path), 'mode': stat.S_IMODE(facts.st_mode),
                'uid': facts.st_uid, 'gid': facts.st_gid}

    def preflight_rotation(self):
        mounts = json.loads(self.run(['docker', 'inspect', '--format', '{{json .Mounts}}', 'spug']))
        self.rotation_text = audit_rotation(mounts, (self.stage / 'resource-audit.logrotate').read_text())
        self.rotation_original = self.rotation_state()
        if self.rotation_original['present']:
            require(self.rotation_original == self.expected_rotation(), 'resource-existing-rotation-unexpected')
            self.verify_rotation()
        require(self.rotation_path.parent.is_dir() and shutil.which('logrotate') is not None,
                'resource-host-logrotate-unavailable')

    def expected_rotation(self):
        return {'present': True, 'sha256': contract.sha(self.rotation_text.encode()),
                'mode': 0o644, 'uid': 0, 'gid': 0}

    def verify_rotation(self):
        require(self.rotation_state() == self.expected_rotation(), 'resource-audit-rotation-mismatch')
        self.run(['logrotate', '--debug', '--state', '/dev/null', str(self.rotation_path)])

    def install_rotation(self):
        require(self.rotation_backed_up and self.rotation_state() == self.rotation_original,
                'resource-rotation-not-backed-up-or-drifted')
        if not self.rotation_original['present']:
            with self.rotation_path.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(self.rotation_text)
            os.chown(self.rotation_path, 0, 0)
            self.rotation_path.chmod(0o644)
        self.verify_rotation()
        self.rotation_installed = True

    def load(self):
        require(base.valid_sha(self.scope_sha256) and base.digest(self.stage / 'scope.json') == self.scope_sha256,
                'resource-external-scope-hash-mismatch')
        stage = load('resource_host_stage', HERE / 'stage.py')
        self.release, self.checks = stage.validate(self.stage, self.revision)
        scope = json.loads((self.stage / 'scope.json').read_text())
        require(contract.sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n')) ==
                scope['release_helpers']['deploy/account-resources/host.py'], 'resource-running-host-code-not-candidate')
        require(base.available_memory() >= 256 * 1024**2 and shutil.disk_usage(self.root).free >= 2 * 1024**3,
                'resource-insufficient-publication-resources')
        for image, expected in ((contract.BASELINE['image_tag'], contract.BASELINE['image_id']),
                                (self.release['image'], self.checks['image_id']),
                                (self.release['rollback_image'], self.checks['rollback_image_id'])):
            require(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image]) == expected,
                    'resource-image-identity-mismatch')
        for image, expected in ((self.release['image'], self.revision),
                                (self.release['rollback_image'], contract.BASELINE['previous_revision'])):
            require(self.run(['docker', 'image', 'inspect', '--format',
                '{{index .Config.Labels "org.opencontainers.image.revision"}}', image]) == expected,
                'resource-image-revision-mismatch')
        self.preservation_probe = schema.build_probe((self.stage / 'fingerprint.py').read_text())

    def fingerprint(self):
        value = json.loads(self.run(['docker', 'exec', '-i', 'spug', 'python3', '-', '--require-idle'],
                                   input=self.preservation_probe, timeout=90))
        require(set(value) == {'business', 'schema', 'migrations'}, 'resource-preservation-envelope-invalid')
        # New table records are checked separately; all legacy guards stay exact.
        core = {k: v for k, v in value['business'].items() if k not in contract.TABLES}
        base.check_fingerprint(core)
        return value

    def identity(self):
        # Both legacy workers import changed permission code and must restart.
        # Only Redis remains alive across this release's maintenance window.
        pid = self.control('pid', 'redis')
        require(re.fullmatch('[1-9][0-9]*', pid), 'resource-redis-process-missing')
        return {'redis_pid': pid,
                'container': self.run(['docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', 'spug']),
                'supervisor_sha256': self.run(['docker', 'exec', 'spug', 'sha256sum', '/etc/supervisord.conf']).split()[0],
                'entrypoint_sha256': self.run(['docker', 'exec', 'spug', 'sha256sum', '/entrypoint.sh']).split()[0],
                'health': self.health_configuration()}

    def expected_identity(self, baseline):
        value = dict(baseline['identity'])
        if self.supervisor_sha: value['supervisor_sha256'] = self.supervisor_sha
        if self.entrypoint_sha: value['entrypoint_sha256'] = self.entrypoint_sha
        return value

    def preflight(self):
        self.load()
        require(self.live_revision() == contract.BASELINE['previous_revision'], 'resource-live-revision-mismatch')
        require(self.run(['docker', 'inspect', '--format', '{{.Image}}', 'spug']) == contract.BASELINE['image_id'],
                'resource-live-container-image-mismatch')
        compose = self.compose_text()
        self.candidate_compose = next_compose(compose, self.revision)
        self.validate_compose()
        self.assert_running()
        before = self.fingerprint()
        require(before['schema'] == contract.BASELINE['schema'] and
                set(before['migrations']) == set(contract.BASELINE['migration_graph']), 'resource-live-schema-or-history-drift')
        files = self.runtime_files()
        base.verify_previous_files(files, self.release)
        verify_live_history(files)
        actor = "import os;os.environ['DJANGO_SETTINGS_MODULE']='spug.settings';import django;django.setup();from apps.account.models import User;assert User.objects.filter(pk=%d,is_supper=True,is_active=True,is_deleted=False).exists();print('verified')" % self.actor_id
        require(self.run(['docker', 'exec', '-w', '/data/spug/spug_api', 'spug', 'python3', '-c', actor]) == 'verified',
                'resource-initialization-actor-not-active-admin')
        self.original_compose = compose
        self.preflight_rotation()
        self.baseline = {'fingerprint': before, 'identity': self.identity(), 'files': files, 'compose': compose}
        return self.baseline

    def backup_configuration(self, baseline):
        self.setup_backup(baseline)
        self.run(['docker', 'cp', 'spug:/entrypoint.sh', str(self.backup_directory / 'entrypoint.sh')])
        (self.backup_directory / 'entrypoint.sh').chmod(0o600)
        require(base.digest(self.backup_directory / 'entrypoint.sh') == baseline['identity']['entrypoint_sha256'],
                'resource-original-entrypoint-backup-mismatch')
        require(self.rotation_state() == self.rotation_original, 'resource-rotation-before-backup-drifted')
        if self.rotation_original['present']:
            target = self.backup_directory / 'resource-audit.logrotate.original'
            target.write_bytes(self.rotation_path.read_bytes())
            target.chmod(0o600)
            require(base.digest(target) == self.rotation_original['sha256'], 'resource-rotation-backup-mismatch')
        self.save('resource-audit-rotation-original.json', self.rotation_original)
        self.rotation_backed_up = True

    def configure_runtime(self, autostart, inject_audit=True, reload=False):
        script = r"""import configparser,hashlib,json,re
from pathlib import Path
p=Path('/etc/supervisord.conf');assert p.is_file() and not p.is_symlink()
c=configparser.ConfigParser(interpolation=None);c.read(p)
names=SERVICES
assert set(n for n in c.sections() if n.startswith('program:'))=={'program:'+n for n in names+('redis',)}
for name in names:
 section='program:'+name
 c.set(section,'autostart',AUTOSTART)
 if INJECT:
  value=c.get(section,'environment',fallback='')
  pattern=r'(^|,)\s*KOPS_RESOURCE_AUDIT_LOG\s*=\s*(?:"[^"]*"|[^,]*)'
  replacement='KOPS_RESOURCE_AUDIT_LOG="/data/logs/resource-access.log"'
  value=re.sub(pattern,lambda m:m[1]+replacement,value) if re.search(pattern,value) else (value+',' if value else '')+replacement
  c.set(section,'environment',value)
with p.open('w') as out:c.write(out)
print(hashlib.sha256(p.read_bytes()).hexdigest())
"""
        script = script.replace('SERVICES', repr(SERVICES)).replace('AUTOSTART', repr('true' if autostart else 'false')).replace('INJECT', repr(inject_audit))
        self.supervisor_sha = self.run(['docker', 'exec', '-i', 'spug', 'python3', '-'], input=script)
        require(base.valid_sha(self.supervisor_sha), 'resource-supervisor-configuration-unverified')
        if reload:
            self.control('reread')
            self.control('update', *SERVICES)

    def close(self):
        self.stop_health()
        self.hold_entrypoint()
        # Persist the gate before stopping processes, so a plain docker restart
        # cannot reopen old permissions after a partially failed publication.
        self.configure_runtime(False, inject_audit=False)
        self.control('stop', *SERVICES, check=False)
        services = self.services()
        require(services['redis'] == 'RUNNING' and all(services[name] in ('STOPPED', 'EXITED') for name in SERVICES),
                'resource-eight-service-maintenance-unverified')
        require(all(self.control('pid', name, check=False) == '0' for name in SERVICES),
                'resource-old-executor-still-running')

    def verify_baseline(self, baseline):
        require(self.fingerprint() == baseline['fingerprint'], 'resource-maintenance-business-drift')
        require(self.identity() == self.expected_identity(baseline), 'resource-maintenance-identity-drift')
        require(self.runtime_files() == baseline['files'] and self.compose_text() == baseline['compose'],
                'resource-maintenance-file-drift')

    def backup_pair(self):
        super().backup()
        self.run(['docker', 'cp', str(self.stage / 'reviewed-scope.json'), 'spug:' + self.destination + '/reviewed-scope.json'])
        require(self.probe({'mode': 'transfer', 'destination': self.destination,
            'expected': {'reviewed-scope.json': self.release['helper_sha256']['reviewed-scope.json']}}) == {'verified': True},
            'resource-scope-transfer-invalid')

    def overlay_api(self):
        self.api_attempted = True
        result = self.helper('api', 'publish')
        base.check_helper_result(result, 'api', 'publish', self.release)
        self.verify_receipt('api', self.baseline)

    def check_model_state(self, pending=False):
        check = """import os
os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'
import django;django.setup()
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState
from django.db.migrations.autodetector import MigrationAutodetector
executor=MigrationExecutor(connection)
plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
assert sorted((m.app_label,m.name,backwards) for m,backwards in plan)==EXPECTED_PLAN
assert not MigrationAutodetector(executor.loader.project_state(),ProjectState.from_apps(apps)).changes(graph=executor.loader.graph)
print('verified')
"""
        expected = sorted((app, '0002_account_resources', False) for app in contract.MODELS) if pending else []
        self.run(['docker', 'exec', '-i', '-w', '/data/spug/spug_api', 'spug', 'python3', '-'],
                 input=check.replace('EXPECTED_PLAN', repr(expected)), timeout=90)

    def migrate(self):
        self.check_model_state(pending=True)
        self.run(['docker', 'exec', '-w', '/data/spug/spug_api', 'spug', 'python3',
                  'manage.py', 'migrate', '--noinput', '--verbosity=0'], timeout=180)
        self.check_model_state(pending=False)

    def verify_additions(self, baseline):
        after = self.fingerprint()
        empty = dict(after, business=dict(after['business']))
        for name in contract.TABLES:
            require(name in after['business'], 'resource-partial-ddl')
            empty['business'][name] = dict(after['business'][name], count=0)
        contract.verify_additive(baseline['fingerprint'], empty, self.release['migrations'])
        require({name: after['schema'][name] for name in contract.TABLES} == self.checks['rehearsal']['new_schema'],
                'resource-applied-schema-not-rehearsed')
        if not self.initialization_attempted:
            contract.verify_additive(baseline['fingerprint'], after, self.release['migrations'])
            self.added = after
        else:
            self.verify_initialization(after)
        return after

    def impact(self, apply=False):
        command = ['docker', 'exec', '-w', '/data/spug/spug_api', 'spug', 'python3', 'manage.py', 'resource_permissions']
        if apply: command += ['--apply', '--actor-id', str(self.actor_id)]
        return json.loads(self.run(command, timeout=90))

    def verify_initialization(self, after):
        require(self.added is not None and self.expected_accounts is not None, 'resource-initialization-boundary-missing')
        require(after['schema'] == self.added['schema'] and after['migrations'] == self.added['migrations'],
                'resource-initialization-schema-or-history-drift')
        mutable = {'account_resource_policies', 'account_resource_audit', 'account_resource_quota_lock'}
        require(all(after['business'][name] == value for name, value in self.added['business'].items() if name not in mutable),
                'resource-initialization-old-business-drift')
        impact = self.impact()
        policies = {row['id']: row['limit'] for row in impact['accounts'] if row['has_policy']}
        require(policies == self.expected_accounts and
                after['business']['account_resource_policies']['count'] == len(policies) and
                after['business']['account_resource_audit']['count'] == len(policies) and
                after['business']['account_resource_quota_lock']['count'] == 1,
                'resource-initialization-not-exact')

    def initialize(self, baseline, upgraded):
        impact = self.impact()
        require(self.fingerprint() == upgraded, 'resource-impact-report-wrote-data')
        self.save('resource-impact-before.json', impact)
        self.expected_accounts = {row['id']: row['used'] for row in impact['accounts'] if not row['admin']}
        self.initialization_attempted = True
        applied = self.impact(apply=True)
        require(set(applied['initialized_account_ids']) == set(self.expected_accounts), 'resource-initialized-account-set-mismatch')
        after = self.fingerprint()
        self.verify_initialization(after)
        self.save('resource-impact-after.json', applied)
        return after

    def overlay_ui(self):
        self.ui_attempted = True
        result = self.helper('ui', 'publish')
        base.check_helper_result(result, 'ui', 'publish', self.release)
        self.verify_receipt('ui', self.baseline)

    def verify_candidate(self, baseline, upgraded):
        self.verify_rotation()
        require(self.fingerprint() == upgraded, 'resource-candidate-business-drift')
        require(self.identity() == self.expected_identity(baseline), 'resource-candidate-identity-drift')
        base.verify_target_files(self.runtime_files(), baseline['files'], self.release)

    def compose_candidate(self):
        require(self.compose_text() == self.original_compose, 'resource-compose-drift')
        self.write_compose(self.candidate_compose)
        self.validate_compose()

    def patch_entrypoint(self):
        script = """import hashlib
from pathlib import Path
p=Path('/entrypoint.sh');s=p.read_text();old=': account-resource-maintenance-migrations-held';new='python3 manage.py migrate --noinput'
assert s.splitlines().count(old)==1 and s.splitlines().count(new)==0
s=s.replace(old,new);assert 'updatedb' not in s and 'makemigrations' not in s
p.write_text(s);print(hashlib.sha256(p.read_bytes()).hexdigest())
"""
        self.entrypoint_sha = self.run(['docker', 'exec', '-i', 'spug', 'python3', '-'], input=script)
        require(base.valid_sha(self.entrypoint_sha), 'resource-entrypoint-patch-unverified')

    def hold_entrypoint(self):
        script = """import hashlib
from pathlib import Path
p=Path('/entrypoint.sh');assert p.is_file() and not p.is_symlink()
s=p.read_text();old='python3 manage.py updatedb';new='python3 manage.py migrate --noinput';hold=': account-resource-maintenance-migrations-held'
assert (s.splitlines().count(old),s.splitlines().count(new),s.splitlines().count(hold)) in ((1,0,0),(0,1,0),(0,0,1))
s=s.replace(old,hold).replace(new,hold)
assert 'updatedb' not in s and 'makemigrations' not in s and 'python3 manage.py migrate' not in s
p.write_text(s);print(hashlib.sha256(p.read_bytes()).hexdigest())
"""
        self.entrypoint_sha = self.run(['docker', 'exec', '-i', 'spug', 'python3', '-'], input=script)
        require(base.valid_sha(self.entrypoint_sha), 'resource-entrypoint-hold-unverified')

    def start_candidate(self):
        self.patch_entrypoint()
        self.configure_runtime(False, reload=True)
        self.control('start', *BACKEND)

    def open(self): self.open_ingress()

    def verify_open(self, baseline, upgraded):
        self.assert_running()
        self.verify_http(self.revision)
        self.verify_candidate(baseline, upgraded)
        # Supervisor reads this on the next restart. No reread/update is needed
        # now: all eight current processes are already started and verified.
        self.configure_runtime(True, reload=False)
        self.verify_candidate(baseline, upgraded)

    def verify_window(self, baseline, upgraded): self.verify_candidate(baseline, upgraded)

    def restore(self, baseline, compatibility):
        if self.ui_attempted:
            self.verify_receipt('ui', baseline)
            result = self.helper('ui', 'restore')
            base.check_helper_result(result, 'ui', 'restore', self.release)
        if self.api_attempted:
            self.verify_receipt('api', baseline)
            mode = 'restore-compat' if compatibility else 'restore'
            result = self.helper('api', mode)
            base.check_helper_result(result, 'api', mode, self.release)
        self.write_compose(next_compose(baseline['compose'], self.revision, rollback=True) if compatibility else baseline['compose'])
        self.validate_compose()

    def verify_restored(self, baseline, upgraded, compatibility):
        require(self.fingerprint() == (upgraded if compatibility else baseline['fingerprint']), 'resource-restored-business-drift')
        require(self.identity() == self.expected_identity(baseline), 'resource-restored-identity-drift')
        expected = dict(baseline['files'])
        if compatibility:
            expected['api'] = [dict(root, **{name: self.release['api_sha256'][name] for name in contract.MIGRATIONS})
                               for root in baseline['files']['api']]
        base.verify_restored_files(self.runtime_files(), expected, self.release['ui_sha256'])
        expected_compose = next_compose(baseline['compose'], self.revision, rollback=True) if compatibility else baseline['compose']
        require(self.compose_text() == expected_compose, 'resource-restored-compose-drift')
        if self.rotation_installed: self.verify_rotation()
        require(all(self.services()[name] in ('STOPPED', 'EXITED') for name in SERVICES), 'resource-rollback-not-closed')

    def record(self, report):
        self.record_number += 1
        self.save('resource-publication-%d.json' % self.record_number,
                  dict(report, revision=self.revision, previous_revision=contract.BASELINE['previous_revision'],
                       backup=str(self.backup_directory)))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or not base.valid_revision(args[0]) or not args[1].isdigit() or int(args[1]) <= 0 or not base.valid_sha(args[2]):
        print(json.dumps({'published': False, 'failure': 'candidate-revision-admin-id-and-frozen-scope-hash-required'}))
        return 2
    os.umask(0o077)
    try:
        with base.publication_lock():
            result = controller.publish(ResourceHost(args[0], int(args[1]), scope_sha256=args[2]))
    except BaseException:
        result = {'published': False, 'failure': 'resource-publication-not-started'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('published') else 1


if __name__ == '__main__': sys.exit(main())
