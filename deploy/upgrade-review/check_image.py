"""Verify the prepared image's runtime and UI against the immutable bundle."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


PHP_MODULE = 'apps/baota/operations/php_update.py'
HEALTH_MODULE = 'apps/setting/health.py'
LOGIN_MODULE = 'apps/baota/login_views.py'
LOGIN_MODULES = (LOGIN_MODULE, 'apps/baota/urls.py')
PROFILES = {'php-update': PHP_MODULE, 'host-report': HEALTH_MODULE, 'operations-ui': None, 'task-results-ui': None,
            'panel-login': LOGIN_MODULES, 'inventory-filters': 'apps/baota/inventory/service.py', 'workbench-ui': None, 'branding-ui': None, 'personal-reminders-ui': None,
            'ssl-renew-diagnostics': 'apps/baota/operations/certificates.py'}


def sha(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('Missing or linked image file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(root, name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
            or ':' in name or path.as_posix() != name):
        raise ValueError('Invalid image manifest path')
    result = root.joinpath(*path.parts)
    if any(parent.is_symlink() for parent in result.parents):
        raise ValueError('Linked image directory')
    return result


def verify(root, stage, revision):
    release = json.loads((stage / 'release.json').read_text())
    if release['revision'] != revision or release['schema_changes'] is not False:
        raise ValueError('Image revision or scope differs')
    profile = release.get('profile', 'php-update')
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError('Unknown image release profile')
    for key in ('api_sha256', 'previous_api_sha256'):
        hashes = release.get(key)
        if profile == 'panel-login':
            if (not isinstance(hashes, dict) or set(hashes) != set(LOGIN_MODULES) or
                    not all((value is None if key == 'previous_api_sha256' and name == LOGIN_MODULE else
                             isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value))
                            for name, value in hashes.items())):
                raise ValueError('Image login overlay differs')
        elif (not isinstance(hashes, dict) or set(hashes) != ({PROFILES[profile]} if PROFILES[profile] else set()) or
                not all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                        for value in hashes.values())):
            raise ValueError('Image API overlay differs')
    preserved = release.get('preserved_api_sha256', {})
    expected_preserved = ({PHP_MODULE, HEALTH_MODULE} if profile in ('operations-ui', 'task-results-ui', 'panel-login', 'inventory-filters', 'workbench-ui', 'branding-ui', 'personal-reminders-ui', 'ssl-renew-diagnostics') else
                          {PHP_MODULE} if profile == 'host-report' else set())
    if (not isinstance(preserved, dict) or set(preserved) != expected_preserved or
            not all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                    for value in preserved.values())):
        raise ValueError('Image preserved API manifest differs')
    ui = json.loads((stage / 'build/ui-release.json').read_text())
    if ui['revision'] != revision or not {'index.html', 'asset-manifest.json'} <= set(ui['sha256']):
        raise ValueError('Image UI manifest differs')
    expectations = [(root / 'spug_api', release['api_sha256']),
                    (root / 'spug_api', preserved),
                    (stage / 'source/spug_api', preserved),
                    (root / 'spug_web/build', dict(ui['sha256'], **{
                        'ui-release.json': sha(stage / 'build/ui-release.json')}))]
    for directory, hashes in expectations:
        for name, expected in hashes.items():
            if sha(relative(directory, name)) != expected:
                raise ValueError('Prepared image content differs')


if __name__ == '__main__':
    verify(Path('/opt/spug'), Path(sys.argv[1]), sys.argv[2])
    print(json.dumps({'revision': sys.argv[2], 'image_content': 'verified'}))
