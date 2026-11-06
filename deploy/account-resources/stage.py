"""Bind prepared migrations, a frozen scope, UI assets and exact image inputs.

No build, transport, backup or publication is performed by this module.
"""
import argparse
import importlib.util
import json
import re
from pathlib import Path
import shutil
import subprocess


HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load('resource_stage_contract', HERE / 'contract.py')
scope_module = load('resource_stage_scope', HERE / 'scope.py')
require, sha = contract.require, contract.sha
CATALOG = 'apps/baota/data/official_api_catalog.json'
CATALOG_RAW = '4ae982653156d0b46c1885fdba4d50c47cb58cc31188214ae4ad97b0d831ddf8'
HELPERS = {'apply-expiry.py': 'deploy/account-resources/overlay.py',
           'reviewed-scope.json': 'deploy/account-resources/reviewed-scope.json',
           'deploy-compact-ui.py': 'deploy/upgrade-review/publish-ui.py',
           'fingerprint.py': 'deploy/upgrade-review/fingerprint.py',
           'release_safety.py': 'deploy/inventory/release_safety.py',
           'resource-audit.logrotate': 'deploy/account-resources/resource-audit.logrotate'}
SOURCES = scope_module.RESOURCE_HELPERS
RECEIPTS = {'stage-files.json', 'checks.json', 'rehearsal-result.json', 'rollback-result.json'}


def tree(root):
    require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), 'stage-root-invalid')
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'stage-symlink-forbidden')
        if path.is_file(): result[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')


def image_updates(preserved, api, history):
    return {**preserved, **api, **history}


def validate_source(root, expected):
    for name, value in expected.items():
        path = root / name
        require(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == value,
                'stage-runtime-source-not-exact')


def materialize(destination, expected, origin):
    require(not destination.exists(), 'image-source-tree-already-exists')
    data = {}
    for name, expected_sha in expected.items():
        require((name == 'manage.py' or name.startswith(('apps/', 'libs/', 'consumer/', 'spug/')))
                and '..' not in Path(name).parts and '\\' not in name, 'image-source-path-invalid')
        path = origin(name)
        require(path.is_file() and not path.is_symlink(), 'image-source-missing-or-linked')
        content = path.read_bytes()
        require(sha(content) == expected_sha, 'image-source-content-mismatch')
        data[name] = content
    for name, content in data.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    require(tree(destination) == expected, 'image-source-tree-not-exact')


def validate_image_facts(release, facts):
    require(set(facts) == {'baseline', 'candidate', 'rollback'}, 'image-facts-incomplete')
    baseline, candidate, rollback = facts['baseline'], facts['candidate'], facts['rollback']
    require(baseline['image_id'] == contract.BASELINE['image_id'], 'image-facts-baseline-mismatch')
    require(candidate['api'] == dict(baseline['api'], **release['image_api_updates']) and
            rollback['api'] == dict(baseline['api'], **release['rollback_image_api_updates']), 'image-facts-tree-delta-not-exact')
    names = {'nginx', 'spug-api', 'spug-ws', 'spug-worker', 'spug-monitor',
             'spug-panel-worker', 'spug-ops-worker', 'spug-scheduler', 'redis'}
    require(set(candidate['programs']) == set(rollback['programs']) == names, 'image-service-set-invalid')
    require(all(v['autostart'] is True and v['autorestart'] is True for v in candidate['programs'].values()),
            'candidate-service-startup-invalid')
    require(all(v['autostart'] is (name == 'redis') and v['autorestart'] is (name == 'redis')
                for name, v in rollback['programs'].items()), 'rollback-image-not-closed')
    require(candidate['entrypoint'] == {'updatedb': 0, 'migrate': 1, 'hold': 0} and
            rollback['entrypoint'] == {'updatedb': 0, 'migrate': 0, 'hold': 1}, 'image-startup-migration-not-exact')
    require(candidate['audit_path'] == '/data/logs/resource-access.log' and set(candidate['models']) == contract.TABLES,
            'candidate-runtime-import-or-audit-invalid')


def bind(prepared, scope, build, catalog, repo=HERE.parents[1]):
    prepared, build, repo = Path(prepared), Path(build), Path(repo)
    reviewed = json.loads((HERE / 'reviewed-scope.json').read_text())
    require(scope['profile'] == contract.PROFILE and scope['previous_revision'] == contract.BASELINE['previous_revision']
            and set(scope['files']) == set(reviewed['runtime_paths']), 'stage-scope-not-exact')
    require(scope_module.freeze(repo, reviewed) == scope, 'stage-candidate-not-current-clean-git')
    require(set(scope['release_helpers']) == set(scope_module.HELPER_PATHS), 'stage-helper-scope-not-exact')
    manifest = json.loads((prepared / 'migration-manifest.json').read_text())
    require(manifest['revision'] == scope['revision'] and manifest['provisional'] is False, 'stage-migrations-not-candidate-bound')
    source = prepared / 'source/spug_api'
    facts = contract.validate_migrations({name: (source / name).read_text() for name in contract.MIGRATIONS})
    require(json.loads(json.dumps(facts)) == manifest['migrations'], 'stage-migration-content-mismatch')
    require(json.loads((HERE / 'migration-plan.json').read_text()) == json.loads(json.dumps(facts)),
            'stage-migration-plan-not-reviewed')
    api = {name[len('spug_api/'):]: value['sha256'] for name, value in scope['files'].items() if name.startswith('spug_api/')}
    previous = {name[len('spug_api/'):]: value['previous_sha256'] for name, value in scope['files'].items() if name.startswith('spug_api/')}
    for name, proof in facts.items(): api[name], previous[name] = proof['sha256'], None
    require(all(sha((source / name).read_bytes()) == value for name, value in api.items()), 'stage-api-content-mismatch')
    def git(*args): return subprocess.check_output(['git', *args], cwd=repo)
    old_names = [name for name in git('ls-tree', '-r', '--name-only', scope['previous_revision'], '--', 'spug_api').decode().splitlines()
                 if scope_module.runtime_file(name)]
    rollback = {}
    for name in old_names:
        relative = name[len('spug_api/'):]
        data = git('show', scope['previous_revision'] + ':' + name).replace(b'\r\n', b'\n')
        target = prepared / 'rollback-source/spug_api' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rollback[relative] = sha(data)
    current_names = [name for name in git('ls-tree', '-r', '--name-only', scope['revision'], '--', 'spug_api').decode().splitlines()
                     if scope_module.runtime_file(name)]
    preserved = {}
    for name in current_names:
        relative = name[len('spug_api/'):]
        if relative in api: continue
        require(relative in rollback, 'unreviewed-new-runtime-source')
        require(sha((source / relative).read_bytes()) == rollback[relative], 'preserved-source-not-baseline')
        preserved[relative] = rollback[relative]
    # The sole observed legacy byte difference is a public catalog's CRLF.
    # Preserve those exact bytes; no other path receives a normalization waiver.
    raw = Path(catalog).read_bytes()
    require(sha(raw) == CATALOG_RAW and raw.replace(b'\r\n', b'\n') == (source / CATALOG).read_bytes(),
            'preserved-public-catalog-not-exact')
    target = prepared / 'preserved-source/spug_api' / CATALOG
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    (prepared / 'rollback-source/spug_api' / CATALOG).write_bytes(raw)
    preserved[CATALOG] = rollback[CATALOG] = CATALOG_RAW
    history = {p.relative_to(source).as_posix(): sha(p.read_bytes()) for p in source.glob('apps/*/migrations/*.py')}
    require(len(history) == 45, 'stage-history-packages-not-exact')
    updates, rollback_updates = image_updates(preserved, api, history), {**rollback, **history}
    materialize(prepared / 'image-source/spug_api', updates,
        lambda name: prepared / 'preserved-source/spug_api' / name if name == CATALOG else source / name)
    materialize(prepared / 'rollback-image-source/spug_api', rollback_updates,
        lambda name: source / name if name in history else prepared / 'rollback-source/spug_api' / name)
    if (prepared / 'build').exists(): raise ValueError('stage-build-already-present')
    require(build.is_dir() and not any(p.is_symlink() for p in build.rglob('*')), 'stage-ui-build-invalid')
    shutil.copytree(build, prepared / 'build')
    ui = tree(prepared / 'build')
    ui.pop('ui-release.json', None)
    require({'index.html', 'asset-manifest.json'} <= set(ui), 'stage-ui-entrypoints-missing')
    write_json(prepared / 'build/ui-release.json', {'revision': scope['revision'], 'sha256': ui})
    helper_hashes = {}
    for name, origin in HELPERS.items():
        content = git('show', scope['revision'] + ':' + origin).replace(b'\r\n', b'\n')
        require(sha(content) == scope['release_helpers'][origin], 'stage-git-helper-mismatch')
        (prepared / name).write_bytes(content)
        helper_hashes[name] = sha((prepared / name).read_bytes())
    for name in SOURCES:
        target = prepared / 'source/deploy/account-resources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        origin = 'deploy/account-resources/' + name
        content = git('show', scope['revision'] + ':' + origin).replace(b'\r\n', b'\n')
        require(sha(content) == scope['release_helpers'][origin], 'stage-git-helper-mismatch')
        target.write_bytes(content)
        helper_hashes[target.relative_to(prepared).as_posix()] = sha(target.read_bytes())
    for name in ('deploy/upgrade-review/publish.py', 'deploy/inventory/release_safety.py'):
        target = prepared / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = git('show', scope['revision'] + ':' + name).replace(b'\r\n', b'\n')
        require(sha(content) == scope['release_helpers'][name], 'stage-git-helper-mismatch')
        target.write_bytes(content)
        helper_hashes[target.relative_to(prepared).as_posix()] = sha(target.read_bytes())
    candidate_docker, rollback_docker = contract.dockerfiles(scope['revision'], facts, api,
        preserved_api_sha256=preserved, rollback_api_sha256=rollback, history_sha256=history)
    (prepared / 'Dockerfile').write_text(candidate_docker, newline='\n')
    (prepared / 'rollback.Dockerfile').write_text(rollback_docker, newline='\n')
    release = {'profile': contract.PROFILE, 'revision': scope['revision'], 'previous_revision': scope['previous_revision'],
        'schema_changes': True, 'api_sha256': api, 'previous_api_sha256': previous,
        'preserved_api_sha256': preserved, 'rollback_api_sha256': rollback, 'history_sha256': history,
        'image_api_updates': updates, 'rollback_image_api_updates': rollback_updates,
        'migrations': facts, 'ui_sha256': ui, 'ui_release_sha256': sha((prepared / 'build/ui-release.json').read_bytes()),
        'helper_sha256': helper_hashes, 'baseline_image_id': contract.BASELINE['image_id'],
        'image': 'spug-baota:' + scope['revision'][:7], 'rollback_image': 'spug-baota:resource-rollback-' + scope['revision'][:7]}
    write_json(prepared / 'scope.json', scope)
    write_json(prepared / 'api-release.json', {'profile': contract.PROFILE, 'revision': scope['revision'], 'sha256': api})
    write_json(prepared / 'release.json', release)
    write_json(prepared / 'stage-files.json', {k: v for k, v in tree(prepared).items() if k not in RECEIPTS})
    return release


def validate(prepared, revision):
    prepared = Path(prepared)
    actual = tree(prepared)
    require('checks.json' in actual and 'stage-files.json' in actual, 'stage-checks-missing')
    pinned = json.loads((prepared / 'stage-files.json').read_text())
    require({k: v for k, v in actual.items() if k not in RECEIPTS} == pinned, 'stage-file-tree-mismatch')
    release = json.loads((prepared / 'release.json').read_text())
    checks = json.loads((prepared / 'checks.json').read_text())
    require(release['profile'] == contract.PROFILE and release['revision'] == revision and
            release['previous_revision'] == contract.BASELINE['previous_revision'], 'stage-release-identity-invalid')
    reviewed = json.loads((HERE / 'reviewed-scope.json').read_text())
    scope = json.loads((prepared / 'scope.json').read_text())
    require(scope['revision'] == revision and scope['previous_revision'] == release['previous_revision'] and
            set(scope['files']) == set(reviewed['runtime_paths']), 'stage-scope-identity-invalid')
    require(set(scope['release_helpers']) == set(scope_module.HELPER_PATHS), 'stage-helper-scope-not-exact')
    expected_api = {n[len('spug_api/'):]: v['sha256'] for n, v in scope['files'].items() if n.startswith('spug_api/')}
    expected_api.update({name: proof['sha256'] for name, proof in release['migrations'].items()})
    require(release['api_sha256'] == expected_api, 'stage-api-set-not-exact')
    expected_previous = {name[len('spug_api/'):]: value['previous_sha256'] for name, value in scope['files'].items()
                         if name.startswith('spug_api/')}
    expected_previous.update({name: None for name in contract.MIGRATIONS})
    require(release['previous_api_sha256'] == expected_previous, 'stage-previous-api-not-exact')
    expected_preserved = {name: value for name, value in scope['candidate_api_sha256'].items() if name not in expected_api}
    expected_preserved[CATALOG] = CATALOG_RAW
    expected_rollback = dict(scope['baseline_api_sha256'], **{CATALOG: CATALOG_RAW})
    require(release['preserved_api_sha256'] == expected_preserved and release['rollback_api_sha256'] == expected_rollback,
            'stage-preserved-or-rollback-runtime-not-exact')
    require(json.loads(json.dumps(contract.validate_migrations({name: (prepared / 'source/spug_api' / name).read_text()
            for name in contract.MIGRATIONS}))) == release['migrations'], 'stage-migration-proof-mismatch')
    require(release['migrations'] == json.loads((HERE / 'migration-plan.json').read_text()), 'stage-migrations-not-reviewed')
    for name, proof in release['migrations'].items():
        require(scope['release_helpers']['deploy/account-resources/migrations/' + name] == proof['sha256'],
                'stage-migration-artifact-not-candidate')
    expected_helpers = {name: scope['release_helpers'][origin] for name, origin in HELPERS.items()}
    expected_helpers.update({'source/deploy/account-resources/' + name:
        scope['release_helpers']['deploy/account-resources/' + name] for name in SOURCES})
    expected_helpers.update({'source/' + name: scope['release_helpers'][name]
                            for name in ('deploy/upgrade-review/publish.py', 'deploy/inventory/release_safety.py')})
    require(release['helper_sha256'] == expected_helpers, 'stage-helper-set-not-exact')
    for name, value in expected_helpers.items(): require(actual.get(name) == value, 'stage-helper-content-mismatch')
    # Pin every existing migration to the observed graph, including blank
    # package markers; the only new operations are the reviewed four files.
    expected_history = {'apps/' + node.split('/')[0] + '/migrations/' + node.split('/')[1] + '.py': proof['sha256']
                        for node, proof in contract.BASELINE['migration_graph'].items()}
    expected_history.update({'apps/' + app + '/migrations/__init__.py': sha(b'')
                             for app in {node.split('/')[0] for node in contract.BASELINE['migration_graph']}})
    expected_history.update({name: proof['sha256'] for name, proof in release['migrations'].items()})
    require(release['history_sha256'] == expected_history, 'stage-migration-history-not-exact')
    updates = image_updates(expected_preserved, expected_api, expected_history)
    rollback_updates = {**expected_rollback, **expected_history}
    require(release['image_api_updates'] == updates and release['rollback_image_api_updates'] == rollback_updates,
            'stage-image-update-set-invalid')
    validate_source(prepared / 'source/spug_api', {**scope['candidate_api_sha256'], **expected_history})
    validate_source(prepared / 'rollback-source/spug_api', expected_rollback)
    require(tree(prepared / 'image-source/spug_api') == updates and
            tree(prepared / 'rollback-image-source/spug_api') == rollback_updates, 'stage-image-copy-tree-invalid')
    candidate_docker, rollback_docker = contract.dockerfiles(revision, contract.MIGRATIONS, expected_api,
        preserved_api_sha256=expected_preserved, rollback_api_sha256=expected_rollback, history_sha256=expected_history)
    require((prepared / 'Dockerfile').read_text() == candidate_docker and
            (prepared / 'rollback.Dockerfile').read_text() == rollback_docker, 'stage-dockerfile-not-derived')
    require(json.loads((prepared / 'api-release.json').read_text()) == {
        'profile': contract.PROFILE, 'revision': revision, 'sha256': expected_api}, 'stage-api-overlay-manifest-invalid')
    ui = tree(prepared / 'build'); ui.pop('ui-release.json')
    require(release['ui_sha256'] == ui and release['ui_release_sha256'] == actual['build/ui-release.json'] and
            json.loads((prepared / 'build/ui-release.json').read_text()) == {'revision': revision, 'sha256': ui},
            'stage-ui-release-not-derived')
    require(checks['release_sha256'] == actual['release.json'] and checks['stage_files_sha256'] == actual['stage-files.json'],
            'stage-check-receipt-not-bound')
    proof = checks['rehearsal']
    require(proof['profile'] == contract.PROFILE and proof['revision'] == revision and proof['provisional'] is False and
            proof['migration_manifest_sha256'] == actual['migration-manifest.json'] and
            proof['old_tables'] == 67 and proof['new_tables'] == 9 and proof['old_migrations'] == 24 and proof['new_migrations'] == 4 and
            set(proof['new_schema']) == contract.TABLES and all(proof.get(k) is True for k in (
                'read_only_impact', 'idempotent_explicit_initialization', 'nonempty_legacy_preserved',
                'historical_orm_reads', 'partial_history_refused')), 'stage-migration-rehearsal-incomplete')
    validate_image_facts(release, checks['image_facts'])
    require(checks['baseline_image_id'] == contract.BASELINE['image_id'] and
            checks['image_id'] == checks['image_facts']['candidate']['image_id'] and
            checks['rollback_image_id'] == checks['image_facts']['rollback']['image_id'], 'stage-image-fact-identity-mismatch')
    for key in ('image_id', 'rollback_image_id'):
        require(isinstance(checks[key], str) and re.fullmatch('sha256:[0-9a-f]{64}', checks[key]),
                'stage-image-id-invalid')
    return release, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--scope', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    args = parser.parse_args()
    value = bind(args.prepared, json.loads(args.scope.read_text()), args.build, args.catalog)
    print(json.dumps({'revision': value['revision'], 'api_files': len(value['api_sha256']),
                      'preserved_api_files': len(value['preserved_api_sha256']), 'rollback_api_files': len(value['rollback_api_sha256'])}))


if __name__ == '__main__': main()
