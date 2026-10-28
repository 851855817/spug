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
INVENTORY_FILE = 'apps/baota/inventory/service.py'
INVENTORY_UI = 'spug_web/src/pages/panel-inventory/index.js'
SSL_FILE = 'apps/baota/operations/certificates.py'
SSL_UI = 'spug_web/src/pages/exec/panel/TaskItem.js'
UI_FILE = 'spug_web/src/pages/exec/operations/RunDetail.js'
TASK_RESULTS_UI = {
    UI_FILE,
    'spug_web/src/pages/exec/panel/TaskDetail.js',
    'spug_web/src/pages/exec/panel/TaskItem.js',
    'spug_web/src/pages/exec/panel/UploadResults.js',
    'spug_web/src/pages/exec/task/Output.js',
    'spug_web/src/pages/exec/transfer/Output.js',
    'spug_web/src/pages/schedule/Info.js',
    'spug_web/src/pages/pipeline/console/Body.js',
}
LOGIN_FILE = 'apps/baota/login_views.py'
LOGIN_MODULES = (LOGIN_FILE, 'apps/baota/urls.py')
LOGIN_UI = {'spug_web/src/pages/baota/index.js', 'spug_web/src/pages/system/role/codes.js'}
APPEARANCE_FILE = 'apps/setting/appearance.py'
APPEARANCE_MODULES = (APPEARANCE_FILE, 'apps/setting/urls.py', 'spug/settings.py')
APPEARANCE_API_TESTS = {'spug_api/apps/setting/test_appearance.py', 'spug_api/spug/test_urls.py'}
REFINED_PALETTES_UI = {
    'spug_web/src/appearance/presets.js', 'spug_web/config-overrides.js',
    'spug_web/src/appearance/theme.less', 'spug_web/src/appearance/AppearanceSetting.js',
    'spug_web/src/appearance/appearance.module.css',
}
APPEARANCE_UI = {
    'spug_web/config-overrides.js',
    'spug_web/src/index.js', 'spug_web/src/index.less', 'spug_web/src/pages/system/setting/index.js',
    'spug_web/src/appearance/presets.js', 'spug_web/src/appearance/store.js',
    'spug_web/src/appearance/Provider.js', 'spug_web/src/appearance/AppearanceSetting.js',
    'spug_web/src/appearance/appearance.module.css', 'spug_web/src/appearance/theme.less',
    'spug_web/src/appearance/antd.dark.less',
    'spug_web/src/branding/branding.module.css',
    'spug_web/src/components/ResizableTable.less', 'spug_web/src/components/index.module.less',
    'spug_web/src/components/reminderDismiss.module.css', 'spug_web/src/layout/layout.module.less',
    'spug_web/src/libs/libs.module.css', 'spug_web/src/pages/config/setting/index.module.less',
    'spug_web/src/pages/dashboard/index.module.css', 'spug_web/src/pages/deploy/app/index.module.css',
    'spug_web/src/pages/deploy/repository/index.module.less', 'spug_web/src/pages/deploy/request/console.module.less',
    'spug_web/src/pages/exec/panel/TaskDetail.module.css', 'spug_web/src/pages/exec/task/index.module.less',
    'spug_web/src/pages/exec/transfer/index.module.less', 'spug_web/src/pages/home/index.module.less',
    'spug_web/src/pages/host/index.module.less', 'spug_web/src/pages/host/selector.module.less',
    'spug_web/src/pages/login/login.module.css', 'spug_web/src/pages/monitor/index.module.less',
    'spug_web/src/pages/pipeline/console/body.module.less', 'spug_web/src/pages/pipeline/console/node.module.less',
    'spug_web/src/pages/pipeline/editor.module.less', 'spug_web/src/pages/pipeline/modules/index.module.less',
    'spug_web/src/pages/pipeline/node.module.less', 'spug_web/src/pages/pipeline/nodeConfig.module.less',
    'spug_web/src/pages/schedule/index.module.less', 'spug_web/src/pages/server-monitor/index.module.less',
    'spug_web/src/pages/ssh/index.module.less', 'spug_web/src/pages/ssh/setting.module.less',
    'spug_web/src/pages/system/health/index.module.css', 'spug_web/src/pages/system/role/index.module.css',
    'spug_web/src/pages/system/setting/index.module.css', 'spug_web/src/pages/welcome/info/index.module.css',
    'spug_web/src/components/AppSelector.js',
    'spug_web/src/components/ResourceMonitorButton.js',
    'spug_web/src/components/ResourceMonitorSwitch.js',
    'spug_web/src/components/TableCard.js',
    'spug_web/src/layout/Notification.js',
    'spug_web/src/pages/alarm/alarm/Table.js',
    'spug_web/src/pages/alarm/contact/Form.js',
    'spug_web/src/pages/config/environment/Table.js',
    'spug_web/src/pages/config/setting/TableView.js',
    'spug_web/src/pages/deploy/app/AddSelect.js',
    'spug_web/src/pages/deploy/app/AutoDeploy.js',
    'spug_web/src/pages/deploy/app/Ext1Setup2.js',
    'spug_web/src/pages/deploy/app/Table.js',
    'spug_web/src/pages/deploy/app/Tips.js',
    'spug_web/src/pages/deploy/repository/Console.js',
    'spug_web/src/pages/deploy/repository/Detail.js',
    'spug_web/src/pages/deploy/repository/Form.js',
    'spug_web/src/pages/deploy/repository/Table.js',
    'spug_web/src/pages/deploy/request/Console.js',
    'spug_web/src/pages/deploy/request/Ext1Form.js',
    'spug_web/src/pages/deploy/request/Ext2Form.js',
    'spug_web/src/pages/deploy/request/HostSelector.js',
    'spug_web/src/pages/deploy/request/Rollback.js',
    'spug_web/src/pages/deploy/request/Table.js',
    'spug_web/src/pages/exec/task/Output.js',
    'spug_web/src/pages/exec/task/index.js',
    'spug_web/src/pages/exec/transfer/Output.js',
    'spug_web/src/pages/exec/transfer/index.js',
    'spug_web/src/pages/home/Notice.js',
    'spug_web/src/pages/host/Group.js',
    'spug_web/src/pages/host/Import.js',
    'spug_web/src/pages/host/Selector.js',
    'spug_web/src/pages/host/Sync.js',
    'spug_web/src/pages/host/Table.js',
    'spug_web/src/pages/monitor/MonitorCard.js',
    'spug_web/src/pages/monitor/Step1.js',
    'spug_web/src/pages/pipeline/Icon.js',
    'spug_web/src/pages/pipeline/Node.js',
    'spug_web/src/pages/pipeline/Table.js',
    'spug_web/src/pages/pipeline/console/Body.js',
    'spug_web/src/pages/pipeline/modules/index.js',
    'spug_web/src/pages/schedule/Step2.js',
    'spug_web/src/pages/server-expiry/Editor.js',
    'spug_web/src/pages/ssh/FileManager.js',
    'spug_web/src/pages/ssh/index.js',
    'spug_web/src/pages/system/setting/BasicSetting.js',
    'spug_web/src/pages/system/setting/KeySetting.js',
}
WORKBENCH_UI = {
    'spug_web/src/pages/home/index.js', 'spug_web/src/pages/home/Todo.js',
    'spug_web/src/pages/home/index.module.less', 'spug_web/src/pages/server-monitor/index.js',
    'spug_web/src/pages/panel-inventory/index.js', 'spug_web/src/pages/server-expiry/index.js',
    'spug_web/src/layout/Sider.js', 'spug_web/src/layout/layout.module.less', 'spug_web/src/layout/navigationOrder.js',
}
BRANDING_UI = {
    'spug_web/src/branding/Brand.js', 'spug_web/src/branding/LegalNotice.js',
    'spug_web/src/branding/source.js', 'spug_web/src/branding/branding.module.css',
    'spug_web/src/pages/ssh/index.js', 'spug_web/src/pages/ssh/Setting.js',
    'spug_web/src/layout/Sider.js', 'spug_web/src/layout/Header.js', 'spug_web/src/layout/Footer.js',
    'spug_web/src/pages/login/index.js', 'spug_web/src/pages/login/login.module.css',
    'spug_web/src/pages/system/setting/About.js', 'spug_web/public/index.html',
    'spug_web/public/kops-20261027/favicon.svg', 'spug_web/public/kops-20261027/manifest.json',
    'spug_web/public/kops-20261027/LICENSE.txt',
}
PERSONAL_REMINDERS_UI = {
    'spug_web/src/components/personalReminders.js', 'spug_web/src/components/ReminderDismiss.js',
    'spug_web/src/components/reminderDismiss.module.css', 'spug_web/src/pages/home/Todo.js',
    'spug_web/src/pages/home/index.module.less', 'spug_web/src/layout/Notification.js',
    'spug_web/src/pages/server-expiry/Reminders.js',
}
INVENTORY_ROLE_PRESETS_UI = {INVENTORY_UI, 'spug_web/src/pages/system/role/PagePerm.js',
                             'spug_web/src/pages/system/role/presets.js'}
PROFILES = {'php-update': API_FILE, 'host-report': HEALTH_FILE, 'operations-ui': None, 'task-results-ui': None,
            'panel-login': LOGIN_MODULES, 'inventory-filters': INVENTORY_FILE, 'workbench-ui': None, 'branding-ui': None, 'personal-reminders-ui': None, 'inventory-role-presets-ui': None,
            'ssl-renew-diagnostics': SSL_FILE, 'site-appearance': APPEARANCE_MODULES,
            'refined-palettes': APPEARANCE_FILE}
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
    if profile == 'refined-palettes':
        runtime = {name for name in names if name.startswith('spug_api/')} - {'spug_api/apps/setting/test_appearance.py'}
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime != {'spug_api/' + APPEARANCE_FILE} or ui != REFINED_PALETTES_UI:
            raise ValueError('Refined palettes only accepts the existing appearance module and fixed five UI changes')
    elif profile == 'site-appearance':
        runtime = {name for name in names if name.startswith('spug_api/')} - APPEARANCE_API_TESTS
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime != {'spug_api/' + name for name in APPEARANCE_MODULES} or ui != APPEARANCE_UI:
            raise ValueError('Appearance release only accepts the fixed appearance API and UI changes')
    elif profile == 'panel-login':
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime != {'spug_api/' + name for name in LOGIN_MODULES} or ui != LOGIN_UI:
            raise ValueError('Login release only accepts the fixed login API and UI changes')
    elif profile == 'inventory-filters':
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime != {'spug_api/' + INVENTORY_FILE} or ui != {INVENTORY_UI}:
            raise ValueError('Inventory filter release only accepts the fixed listing and UI changes')
    elif profile == 'ssl-renew-diagnostics':
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        if runtime != {'spug_api/' + SSL_FILE} or ui != {SSL_UI}:
            raise ValueError('SSL diagnostics release only accepts the fixed certificate adapter and task item UI')
    elif profile in ('operations-ui', 'task-results-ui', 'workbench-ui', 'branding-ui', 'personal-reminders-ui', 'inventory-role-presets-ui'):
        ui = {name for name in names if name.startswith('spug_web/') and not name.endswith('.test.js')}
        expected = INVENTORY_ROLE_PRESETS_UI if profile == 'inventory-role-presets-ui' else PERSONAL_REMINDERS_UI if profile == 'personal-reminders-ui' else BRANDING_UI if profile == 'branding-ui' else WORKBENCH_UI if profile == 'workbench-ui' else TASK_RESULTS_UI if profile == 'task-results-ui' else {UI_FILE}
        if runtime or ui != expected:
            raise ValueError('UI release only accepts the fixed reviewed page changes')
    elif runtime != {'spug_api/' + module}:
        raise ValueError('This release only accepts the selected reviewed runtime change')


def write_manifests(stage, revision, previous_revision, previous_content,
                    profile='php-update', preserved_content=None):
    module = profile_module(profile)
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (revision, previous_revision)):
        raise ValueError('Invalid release revision')
    if profile == 'site-appearance':
        if (not isinstance(previous_content, dict) or set(previous_content) != set(APPEARANCE_MODULES) or
                previous_content[APPEARANCE_FILE] is not None or
                not all(isinstance(previous_content[name], bytes) for name in APPEARANCE_MODULES[1:])):
            raise ValueError('Appearance baseline must have URLs/settings and no appearance module')
        api_hashes = {name: sha256(stage / 'source/spug_api' / name) for name in APPEARANCE_MODULES}
        previous_hashes = {name: hashlib.sha256(content).hexdigest() if content is not None else None
                           for name, content in previous_content.items()}
    elif profile == 'panel-login':
        if (not isinstance(previous_content, dict) or set(previous_content) != set(LOGIN_MODULES) or
                previous_content[LOGIN_FILE] is not None or
                not isinstance(previous_content['apps/baota/urls.py'], bytes)):
            raise ValueError('Login baseline must have URLs and no existing login module')
        api_hashes = {name: sha256(stage / 'source/spug_api' / name) for name in LOGIN_MODULES}
        previous_hashes = {name: hashlib.sha256(content).hexdigest() if content is not None else None
                           for name, content in previous_content.items()}
    else:
        if profile in ('inventory-filters', 'ssl-renew-diagnostics', 'refined-palettes') and not isinstance(previous_content, bytes):
            raise ValueError('Selected API module must already exist in the baseline')
        api_hashes = {module: sha256(stage / 'source/spug_api' / module)} if module else {}
        previous_hashes = {module: hashlib.sha256(previous_content).hexdigest()} if module else {}
    preserved_hashes = {}
    if profile in ('operations-ui', 'task-results-ui', 'panel-login', 'inventory-filters', 'workbench-ui', 'branding-ui', 'personal-reminders-ui', 'inventory-role-presets-ui', 'ssl-renew-diagnostics', 'site-appearance', 'refined-palettes'):
        if ((profile in ('operations-ui', 'task-results-ui', 'workbench-ui', 'branding-ui', 'personal-reminders-ui', 'inventory-role-presets-ui') and previous_content is not None) or not isinstance(preserved_content, dict) or
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
    # CRA's launcher spawns another Node process; NODE_OPTIONS reaches that
    # process as well, unlike a --require passed only to the launcher CLI.
    preload = (repo / 'deploy/upgrade-review/build-limit.cjs').as_posix()
    environment = dict(os.environ, NODE_OPTIONS='--require=' + json.dumps(preload) + ' --openssl-legacy-provider --max-old-space-size=768 --max-semi-space-size=8',
                       GENERATE_SOURCEMAP='false', CI='false', REACT_APP_RELEASE_REVISION=revision)
    subprocess.run([args.node, '--require', str(repo / 'deploy/upgrade-review/build-limit.cjs'),
                    'node_modules/react-app-rewired/bin/index.js', 'build'],
                   cwd=repo / 'spug_web', env=environment, check=True, timeout=600)
    if git('rev-parse', 'HEAD').decode().strip() != revision or git('status', '--porcelain').strip():
        raise ValueError('Checkout changed during build')
    stage = repo.parent / ('upgrade-' + revision[:7])
    stage.mkdir(exist_ok=False)
    shutil.copytree(repo / 'spug_web/build', stage / 'build')
    # NUL delimiters preserve Git paths containing Chinese characters, spaces or newlines.
    for name in filter(None, git('ls-files', '-z', 'spug_api', 'deploy', 'spug_web/public', 'LICENSE').decode().split('\0')):
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
    if args.profile == 'site-appearance':
        if git('ls-tree', '--name-only', previous, '--', 'spug_api/' + APPEARANCE_FILE).strip():
            raise ValueError('Appearance module already exists in baseline; design a new reviewed profile')
        old_content = {APPEARANCE_FILE: None, **{name:
            git('show', previous + ':spug_api/' + name).replace(b'\r\n', b'\n')
            for name in APPEARANCE_MODULES[1:]}}
    elif args.profile == 'panel-login':
        if git('ls-tree', '--name-only', previous, '--', 'spug_api/' + LOGIN_FILE).strip():
            raise ValueError('Login module already exists in baseline; design a new reviewed profile')
        old_content = {LOGIN_FILE: None, 'apps/baota/urls.py':
                       git('show', previous + ':spug_api/apps/baota/urls.py').replace(b'\r\n', b'\n')}
    else:
        old_content = git('show', previous + ':spug_api/' + module).replace(b'\r\n', b'\n') if module else None
    if args.profile in ('operations-ui', 'task-results-ui', 'panel-login', 'inventory-filters', 'workbench-ui', 'branding-ui', 'personal-reminders-ui', 'inventory-role-presets-ui', 'ssl-renew-diagnostics', 'site-appearance', 'refined-palettes'):
        preserved_content = {name: git('show', previous + ':spug_api/' + name).replace(b'\r\n', b'\n')
                             for name in (API_FILE, HEALTH_FILE)}
    else:
        preserved_content = (git('show', previous + ':spug_api/' + API_FILE).replace(b'\r\n', b'\n')
                             if args.profile == 'host-report' else None)
    write_manifests(stage, revision, previous, old_content, args.profile, preserved_content)
    modules = APPEARANCE_MODULES if args.profile == 'site-appearance' else LOGIN_MODULES if args.profile == 'panel-login' else (module,) if module else ()
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
