"""Finish only the measured 15d69ee initialization/display-size interruption.

No migration, policy initialization, API overwrite or old-code reopening occurs.
The original stage and checks remain immutable. A separately reviewed bundle
supplies the narrow, independently rehearsed schema metadata correction.
"""
import configparser
from contextlib import contextmanager
import copy
from datetime import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys

CANDIDATE = '15d69eef7d73abd5078a3cf5e38411d4da9a15a1'
SCOPE_SHA = '74acac949f9403c6701bcffda6c390be61baee7a34f4bb65466289e46ee50925'
PROFILE = 'account-resources-display-size-recovery-v2'


def require(ok, reason):
    if not ok: raise ValueError(reason)


def sha(data): return hashlib.sha256(data).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recovery_probe(stable, legacy):
    # Only this timed recovery uses a shorter prospective window. All active
    # jobs, leases, queues, credentials and business guards remain identical.
    anchor = 'horizon = now + timedelta(minutes=15)'
    require(legacy.count(anchor) == 1, 'recovery-idle-window-anchor')
    return stable.build_probe(legacy.replace(anchor, 'horizon = now + timedelta(minutes=5)'))


@contextmanager
def deadline():
    def expired(signum, frame): raise TimeoutError('recovery-time-budget-exceeded')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(120)
    try: yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def validate_initialization(before, after, proof, impact_before, impact_now, rows, tables):
    require(set(after['schema']) == set(before['schema']) | tables, 'recovery-schema-set')
    require(all(after['schema'].get(k) == v for k, v in before['schema'].items()), 'recovery-old-schema-drift')
    require(all(after['business'].get(k) == v for k, v in before['business'].items()), 'recovery-old-business-drift')
    require({k: after['schema'][k] for k in tables} == proof['new_schema'], 'recovery-new-structure-drift')
    require(rows['policies'] == [{'user_id': 2, 'server_limit': 0}] and rows['locks'] == [{'id': 1}],
            'recovery-policy-or-lock-unexpected')
    require(len(rows['audits']) == 1, 'recovery-audit-count')
    audit = rows['audits'][0]
    require(set(audit) == {'id', 'actor_id', 'account_id', 'event', 'detail', 'created_at'} and
            audit['id'] == 1 and audit['actor_id'] == 1 and audit['account_id'] == 2 and
            audit['event'] == 'legacy_quota_initialized' and audit['detail'] == {'limit': 0} and
            isinstance(audit['created_at'], str) and bool(audit['created_at']), 'recovery-audit-unexpected')
    created = datetime.fromisoformat(audit['created_at'])
    require(created.tzinfo is None and datetime(2026, 11, 6, 22, 37, 11) <= created <
            datetime(2026, 11, 6, 22, 40), 'recovery-audit-time-unexpected')
    expected = copy.deepcopy(impact_before)
    ordinary = [a for a in expected['accounts'] if not a['admin']]
    require(len(ordinary) == 1 and ordinary[0]['id'] == 2 and ordinary[0]['used'] == 0 and
            ordinary[0]['has_policy'] is False, 'recovery-impact-baseline-unexpected')
    ordinary[0].update(has_policy=True, limit=0)
    require(impact_now == expected, 'recovery-impact-drift')
    counts = {k: after['business'][k]['count'] for k in tables}
    require(counts == {k: int(k in {'account_resource_policies', 'account_resource_audit',
                                  'account_resource_quota_lock'}) for k in tables}, 'recovery-new-row-count')
    derived = copy.deepcopy(after)
    for table in tables:
        derived['business'][table] = {'count': 0, 'digest': sha(json.dumps([], sort_keys=True).encode())}
    return derived


def continue_candidate(host, baseline, upgraded):
    report = {'published': False, 'maintenance_closed': True, 'recovery_profile': PROFILE}
    step = 'recovery-evidence'
    try:
        with deadline():
            host.save('resource-recovery-evidence.json', host.recovery_evidence)
            host.save('resource-impact-after.json', host.impact())
            step = 'ui-overlay'; host.overlay_ui()
            host.verify_candidate(baseline, upgraded)
            step = 'future-compose'; host.compose_candidate()
            step = 'backend-start'
            require(host.fingerprint() == upgraded, 'recovery-start-window-or-data-drift')
            host.start_candidate()
            host.verify_candidate(baseline, upgraded)
            step = 'open-ingress'; host.open()
            host.verify_open(baseline, upgraded)
            step = 'resume-health'; host.resume_health()
            report.update(published=True, maintenance_closed=False)
            host.record(report)
        return report
    except BaseException:
        report.update(published=False, failure=step)
        try:
            host.close()
            report['maintenance_closed'] = True
        except BaseException: report['maintenance_closed'] = False
        try: host.record(report)
        except BaseException: pass
        return report


def prepare(bundle, manifest_sha):
    require(not any(p.is_symlink() for p in (bundle, *bundle.parents)), 'recovery-bundle-linked')
    manifest_path = bundle / 'recovery.json'
    require(sha(manifest_path.read_bytes()) == manifest_sha, 'recovery-external-manifest-hash')
    manifest = json.loads(manifest_path.read_text())
    require(manifest['profile'] == PROFILE and manifest['candidate_revision'] == CANDIDATE and
            manifest['scope_sha256'] == SCOPE_SHA, 'recovery-manifest-identity')
    names = {'recover.py', 'schema.py', 'rehearsal.py', 'contract.py', 'production-baseline.json', 'rehearsal.json'}
    require(set(manifest['files']) == names and {p.name for p in bundle.iterdir()} == names | {'recovery.json'},
            'recovery-bundle-files')
    for name, expected in manifest['files'].items():
        p = bundle / name
        require(p.is_file() and not p.is_symlink() and sha(p.read_bytes()) == expected, 'recovery-helper-hash')
    stage = Path('/opt/spug/account-resources-15d69ee')
    require(sha((stage / 'checks.json').read_bytes()) == manifest['original_checks_sha256'], 'recovery-original-checks-changed')
    original = load('resource_original_recovery_host', stage / 'source/deploy/account-resources/host.py')
    stable = load('resource_stable_recovery_probe', bundle / 'schema.py')
    require(stable.contract.BASELINE == original.contract.BASELINE, 'recovery-baseline-expanded')
    host = original.ResourceHost(CANDIDATE, 1, scope_sha256=SCOPE_SHA)
    host.load()
    proof = json.loads((bundle / 'rehearsal.json').read_text())
    require(proof['profile'] == original.contract.PROFILE and proof['revision'] == CANDIDATE and
            proof['migration_manifest_sha256'] == sha((stage / 'migration-manifest.json').read_bytes()) and
            proof['initialization_schema_stable'] is True and proof['provisional'] is False and
            proof['database_version'] == '10.11.19-MariaDB-ubu2204' and
            proof['database_collation'] == 'utf8mb4_unicode_ci' and proof['database_charset'] == 'utf8mb4' and
            (proof['old_tables'], proof['new_tables'], proof['old_migrations'], proof['new_migrations']) == (67, 9, 24, 4) and
            set(proof['new_schema']) == original.contract.TABLES and all(proof[k] is True for k in (
                'read_only_impact', 'idempotent_explicit_initialization', 'nonempty_legacy_preserved',
                'historical_orm_reads', 'partial_history_refused')), 'recovery-stable-proof-incomplete')
    require(proof['schema_helper_sha256'] == manifest['files']['schema.py'] and
            proof['rehearsal_helper_sha256'] == manifest['files']['rehearsal.py'], 'recovery-proof-helper-mismatch')
    backup = Path(manifest['backup'])
    require(backup.parent == Path('/opt/spug/backups') and '-before-upgrade-15d69ee-' in backup.name and
            not any(p.is_symlink() for p in (backup, *backup.parents)), 'recovery-backup-path')
    original.base.verify_backup_manifests(backup)
    for name in ('backup-content.json', 'resource-impact-before.json', 'resource-publication-1.json'):
        require(sha((backup / name).read_bytes()) == manifest['backup_sha256'][name], 'recovery-backup-anchor-changed')
    failure = json.loads((backup / 'resource-publication-1.json').read_text())
    require(failure['published'] is False and failure['failure'] == 'explicit-legacy-initialization' and
            failure['maintenance_closed'] is True and failure['rolled_back'] is False and
            failure['revision'] == CANDIDATE and not (backup / 'resource-publication-2.json').exists() and
            not (backup / 'resource-recovery-evidence.json').exists(), 'recovery-failure-boundary')
    baseline = json.loads((backup / 'preservation-before.json').read_text())
    require(host.services() == {n: 'RUNNING' if n == 'redis' else 'STOPPED'
                               for n in (*original.SERVICES, 'redis')}, 'recovery-not-closed')
    require(host.identity()['container'] == baseline['identity']['container'] and
            host.control('pid', 'redis') == baseline['identity']['redis_pid'] and
            host.health_configuration() == baseline['identity']['health'], 'recovery-process-or-health-drift')
    require(all(host.run(['systemctl', 'is-active', unit], check=False) == 'inactive' for unit in (
        'spug-platform-health.timer', 'spug-platform-health.service')), 'recovery-health-not-stopped')
    config = configparser.ConfigParser(interpolation=None)
    config.read_string((backup / 'supervisord.conf').read_text())
    for name in original.SERVICES: config.set('program:' + name, 'autostart', 'false')
    output = io.StringIO(); config.write(output)
    host.supervisor_sha = sha(output.getvalue().encode())
    entrypoint = (backup / 'entrypoint.sh').read_text()
    require(entrypoint.splitlines().count('python3 manage.py updatedb') == 1, 'recovery-entrypoint-baseline')
    host.entrypoint_sha = sha(entrypoint.replace('python3 manage.py updatedb',
        ': account-resource-maintenance-migrations-held').encode())
    require(host.identity() == host.expected_identity(baseline), 'recovery-persistent-gate-drift')
    require(host.compose_text() == baseline['compose'], 'recovery-compose-drift')
    files = host.runtime_files()
    require(files['api'] == [dict(root, **host.release['api_sha256']) for root in baseline['files']['api']] and
            files['ui'] == baseline['files']['ui'] and files['ui_revisions'] == baseline['files']['ui_revisions'],
            'recovery-runtime-boundary-drift')
    host.verify_receipt('api', baseline)
    host.check_model_state(pending=False)
    host.preflight_rotation()
    host.rotation_original = json.loads((backup / 'resource-audit-rotation-original.json').read_text())
    host.rotation_installed = host.rotation_backed_up = True
    host.verify_rotation()
    host.preservation_probe = recovery_probe(stable, (stage / 'fingerprint.py').read_text())
    host.checks = copy.deepcopy(host.checks)
    host.checks['rehearsal'] = proof
    upgraded = host.fingerprint()
    query = """import os,json
os.environ['DJANGO_SETTINGS_MODULE']='spug.settings'
import django;django.setup()
from apps.account.resource_models import AccountResourcePolicy,ResourceQuotaLock,ResourceAccessAudit
from apps.account.models import User
assert User.objects.filter(pk=1,is_supper=True,is_active=True,is_deleted=False).exists()
print(json.dumps({'policies':list(AccountResourcePolicy.objects.order_by('pk').values()),'locks':list(ResourceQuotaLock.objects.values()),'audits':list(ResourceAccessAudit.objects.order_by('pk').values())},default=str))
"""
    rows = json.loads(host.run(['docker', 'exec', '-i', '-w', '/data/spug/spug_api', 'spug', 'python3', '-'], input=query))
    impact_before = json.loads((backup / 'resource-impact-before.json').read_text())
    host.added = validate_initialization(baseline['fingerprint'], upgraded, proof, impact_before,
                                        host.impact(), rows, original.contract.TABLES)
    original.contract.verify_additive(baseline['fingerprint'], host.added, host.release['migrations'])
    host.initialization_attempted = host.api_attempted = True
    host.expected_accounts = {2: 0}
    host.verify_initialization(upgraded)
    host.baseline = baseline
    host.backup_directory = backup
    host.health_state = json.loads((backup / 'health-state.json').read_text())
    host.original_compose = baseline['compose']
    host.candidate_compose = original.next_compose(baseline['compose'], CANDIDATE)
    host.record_number = 1
    host.recovery_evidence = {'profile': PROFILE, 'manifest_sha256': manifest_sha,
        'candidate_revision': CANDIDATE, 'stable_rehearsal_sha256': manifest['files']['rehearsal.json'],
        'derived_empty_additions': True, 'derived_snapshot_sha256': sha(json.dumps(host.added, sort_keys=True).encode()),
        'old_business_preserved': True, 'initialization_rows_verified': True, 'ddl_or_initialization_repeated': False,
        'prospective_ssl_window_minutes': 5, 'continuation_deadline_seconds': 120}
    return host, baseline, upgraded


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or len(args[0]) != 64: return 2
    os.umask(0o077)
    try:
        host, baseline, upgraded = prepare(Path(__file__).resolve().parent, args[0])
        lock_base = load('recovery_lock_base', host.stage / 'source/deploy/upgrade-review/publish.py')
        with lock_base.publication_lock():
            # Revalidate under the publication lock before the first write.
            host, baseline, upgraded = prepare(Path(__file__).resolve().parent, args[0])
            result = continue_candidate(host, baseline, upgraded)
    except BaseException:
        result = {'published': False, 'maintenance_closed': None, 'failure': 'recovery-preflight'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('published') else 1


if __name__ == '__main__': sys.exit(main())
