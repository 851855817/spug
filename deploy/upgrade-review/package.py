"""Build a fixed, reviewed runtime or UI patch on the live baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


API_FILE = 'apps/baota/operations/php_update.py'
HEALTH_FILE = 'apps/setting/health.py'
UI_FILE = 'spug_web/src/pages/exec/operations/RunDetail.js'
LOGIN_FILE = 'apps/baota/login_views.py'
LOGIN_MODULES = (LOGIN_FILE, 'apps/baota/urls.py')
LOGIN_UI = {'spug_web/src/pages/baota/index.js', 'spug_web/src/pages/system/role/codes.js'}
PROFILES = {'php-update': API_FILE, 'host-report': HEALTH_FILE, 'operations-ui': None,
            'panel-login': LOGIN_MODULES}
HELPERS = ('apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py')


def sha256(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('Missing or linked release file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile_module(profile):
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError('Unknown release profile')
    return PROFILES[profile]


def validate_runtime_changes(names, profile='php-update'):
    module = profile_module(profile)
    runtime = {name for name in names if name.startswith('spug_api/')
               and not Path(name).name.startswith('test')}
    if profile == 'panel-login':
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime != {'spug_api/' + name for name in LOGIN_MODULES} or ui != LOGIN_UI:
            raise ValueError('Login release only accepts the fixed login API and UI changes')
    elif profile == 'operations-ui':
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime or ui != {UI_FILE}:
            raise ValueError('UI release only accepts the fixed operations page change')
    elif runtime != {'spug_api/' + module}:
        raise ValueError('This release only accepts the selected reviewed runtime change')


def write_manifests(stage, revision, previous_revision, previous_content,
                    profile='php-update', preserved_content=None):
    module = profile_module(profile)
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (revision, previous_revision)):
        raise ValueError('Invalid release revision')
    if profile == 'panel-login':
        if (not isinstance(previous_content, dict) or set(previous_content) != set(LOGIN_MODULES) or
                previous_content[LOGIN_FILE] is not None or
                not isinstance(previous_content['apps/baota/urls.py'], bytes)):
            raise ValueError('Login baseline must have URLs and no existing login module')
        api_hashes = {name: sha256(stage / 'source/spug_api' / name) for name in LOGIN_MODULES}
        previous_hashes = {name: hashlib.sha256(content).hexdigest() if content is not None else None
                           for name, content in previous_content.items()}
    else:
        api_hashes = {module: sha256(stage / 'source/spug_api' / module)} if module else {}
        previous_hashes = {module: hashlib.sha256(previous_content).hexdigest()} if module else {}
    preserved_hashes = {}
    if profile in ('operations-ui', 'panel-login'):
        if ((profile == 'operations-ui' and previous_content is not None) or not isinstance(preserved_content, dict) or
                set(preserved_content) != {API_FILE, HEALTH_FILE} or
                not all(isinstance(value, bytes) for value in preserved_content.values())):
            raise ValueError('Missing fixed UI baseline module contents')
        preserved_hashes = {name: hashlib.sha256(value).hexdigest() for name, value in preserved_content.items()}
        if any(sha256(stage / 'source/spug_api' / name) != value for name, value in preserved_hashes.items()):
            raise ValueError('UI preserved modules differ from the baseline')
    elif profile == 'host-report':
        if not isinstance(preserved_content, bytes):
            raise ValueError('Missing baseline PHP content')
        preserved_hashes[API_FILE] = hashlib.sha256(preserved_content).hexdigest()
        if sha256(stage / 'source/spug_api' / API_FILE) != preserved_hashes[API_FILE]:
            raise ValueError('Preserved PHP differs from the baseline')
    elif preserved_content is not None:
        raise ValueError('PHP profile cannot preserve additional API modules')
    build = stage / 'build'
    for required in ('index.html', 'asset-manifest.json'):
        sha256(build / required)
    ui_hashes = {path.relative_to(build).as_posix(): sha256(path) for path in sorted(build.rglob('*'))
                 if path.is_file() and path.name != 'ui-release.json'}
    if any(path.is_symlink() for path in build.rglob('*')):
        raise ValueError('Linked UI asset')
    (build / 'ui-release.json').write_text(json.dumps({'revision': revision, 'sha256': ui_hashes}, indent=2))
    (stage / 'api-release.json').write_text(json.dumps({'revision': revision, 'sha256': api_hashes}, indent=2))
    manifest = {'revision': revision, 'previous_revision': previous_revision, 'profile': profile,
                'api_sha256': api_hashes, 'previous_api_sha256': previous_hashes,
                'preserved_api_sha256': preserved_hashes,
                'helper_sha256': {name: sha256(stage / name) for name in HELPERS},
                'image': 'spug-baota:' + revision[:7], 'schema_changes': False}
    (stage / 'release.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous_revision')
    parser.add_argument('--node', required=True)
    parser.add_argument('--profile', choices=tuple(PROFILES), default='php-update')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    def git(*values):
        return subprocess.check_output(['git', *values], cwd=repo)
    revision = git('rev-parse', 'HEAD').decode().strip()
    previous = git('rev-parse', '--verify', args.previous_revision + '^{commit}').decode().strip()
    if git('status', '--porcelain').strip():
        raise ValueError('Package only a clean reviewed checkout')
    module = profile_module(args.profile)
    validate_runtime_changes(git('diff', '--name-only', previous, revision).decode().splitlines(), args.profile)
    environment = dict(os.environ, NODE_OPTIONS='--openssl-legacy-provider --max-old-space-size=384 --max-semi-space-size=8',
                       GENERATE_SOURCEMAP='false', CI='false')
    subprocess.run([args.node, '--require', str(repo / 'deploy/upgrade-review/build-limit.cjs'),
                    'node_modules/react-app-rewired/bin/index.js', 'build'],
                   cwd=repo / 'spug_web', env=environment, check=True, timeout=600)
    if git('rev-parse', 'HEAD').decode().strip() != revision or git('status', '--porcelain').strip():
        raise ValueError('Checkout changed during build')
    stage = repo.parent / ('upgrade-' + revision[:7])
    stage.mkdir(exist_ok=False)
    shutil.copytree(repo / 'spug_web/build', stage / 'build')
    for name in git('ls-files', 'spug_api', 'deploy').decode().splitlines():
        source = repo / name
        if source.is_symlink():
            raise ValueError('Linked source file')
        target = stage / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        if target.suffix in ('.py', '.ini', '.sh', '.service', '.timer'):
            content = content.replace(b'\r\n', b'\n')
        target.write_bytes(content)
    for name, source in {
        'apply-expiry.py': repo / 'deploy/expiry/apply-expiry.py',
        'deploy-compact-ui.py': repo / 'deploy/upgrade-review/publish-ui.py',
        'fingerprint.py': repo / 'deploy/upgrade-review/fingerprint.py',
        'release_safety.py': repo / 'deploy/inventory/release_safety.py',
    }.items():
        (stage / name).write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))
    if args.profile == 'panel-login':
        if git('ls-tree', '--name-only', previous, '--', 'spug_api/' + LOGIN_FILE).strip():
            raise ValueError('Login module already exists in baseline; design a new reviewed profile')
        old_content = {LOGIN_FILE: None, 'apps/baota/urls.py':
                       git('show', previous + ':spug_api/apps/baota/urls.py').replace(b'\r\n', b'\n')}
    else:
        old_content = git('show', previous + ':spug_api/' + module).replace(b'\r\n', b'\n') if module else None
    if args.profile in ('operations-ui', 'panel-login'):
        preserved_content = {name: git('show', previous + ':spug_api/' + name).replace(b'\r\n', b'\n')
                             for name in (API_FILE, HEALTH_FILE)}
    else:
        preserved_content = (git('show', previous + ':spug_api/' + API_FILE).replace(b'\r\n', b'\n')
                             if args.profile == 'host-report' else None)
    write_manifests(stage, revision, previous, old_content, args.profile, preserved_content)
    modules = LOGIN_MODULES if args.profile == 'panel-login' else (module,) if module else ()
    (stage / 'Dockerfile').write_text(
        'FROM spug-baota:' + previous[:7] + '\nARG SOURCE_REVISION\n'
        'LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"\n'
        + ''.join('COPY source/spug_api/' + name + ' /opt/spug/spug_api/' + name + '\n' for name in modules) +
        'COPY build/ /opt/spug/spug_web/build/\n')
    archive = stage.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as output:
        output.add(stage, arcname=stage.name)
    report = {'revision': revision, 'previous_revision': previous, 'archive': str(archive), 'sha256': sha256(archive)}
    (repo.parent / 'upgrade-release.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
