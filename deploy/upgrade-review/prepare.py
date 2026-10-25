"""Verify an immutable patch bundle and run isolated checks; never change live data."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


def image_identity(description, revision):
    identity = description['Id']
    if (not isinstance(identity, str) or not re.fullmatch('sha256:[0-9a-f]{64}', identity)
            or description['Config']['Labels'].get('org.opencontainers.image.revision') != revision):
        raise ValueError('Prepared image identity differs')
    return identity


def safe_extract(archive, root, stage_name):
    if (root / stage_name).exists():
        raise ValueError('Release stage already exists')
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if not 1 <= len(members) <= 20000 or sum(member.size for member in members) > 500 * 1024**2:
            raise ValueError('Release archive exceeds limits')
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts
                    or path.parts[0] != stage_name or not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError('Unsafe release archive member')
            seen.add(member.name)
        source.extractall(root, filter='data')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision')
    parser.add_argument('archive_sha256')
    args = parser.parse_args()
    revision = args.revision
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or not re.fullmatch(r'[0-9a-f]{64}', args.archive_sha256):
        raise ValueError('Invalid release identity')
    root = Path('/opt/spug')
    archive = root / ('upgrade-' + revision[:7] + '.tar.gz')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != args.archive_sha256:
        raise ValueError('Release archive digest differs')
    if shutil.disk_usage(root).free < 2 * 1024**3:
        raise ValueError('Insufficient staging space')
    safe_extract(archive, root, 'upgrade-' + revision[:7])
    stage = root / ('upgrade-' + revision[:7])
    manifest = json.loads((stage / 'release.json').read_text())
    previous = manifest['previous_revision']
    if manifest['revision'] != revision or not re.fullmatch(r'[0-9a-f]{40}', previous) or manifest['schema_changes'] is not False:
        raise ValueError('Release identity or scope differs')
    if set(manifest['helper_sha256']) != {'apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'}:
        raise ValueError('Release helper set differs')
    for name, digest in manifest['helper_sha256'].items():
        if hashlib.sha256((stage / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Release helper digest differs')
    image = 'spug-baota:' + revision[:7]
    if image != manifest['image']:
        raise ValueError('Release image differs')
    inspect = subprocess.check_output(['docker', 'image', 'inspect', '--format',
        '{{index .Config.Labels "org.opencontainers.image.revision"}}', 'spug-baota:' + previous[:7]], text=True).strip()
    if inspect != previous:
        raise ValueError('Baseline image revision differs')
    subprocess.run(['docker', 'build', '--network', 'none', '--build-arg', 'SOURCE_REVISION=' + revision,
                    '-t', image, str(stage)], check=True, timeout=300)
    image_id = image_identity(json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', image], text=True))[0], revision)
    source = stage / 'source'
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
        '--pids-limit', '32', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', str(stage) + ':/release:ro',
        '--entrypoint', 'python3', image_id, '/release/source/deploy/upgrade-review/check_image.py',
        '/release', revision], check=True, timeout=90)
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
        '--memory', '256m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'PYTHONUNBUFFERED=1', '-v', str(source / 'spug_api') + ':/source/spug_api:ro',
        '--workdir', '/source/spug_api', '--entrypoint', 'python3', image_id]
    modules = ['apps.baota.' + path.stem for path in sorted((source / 'spug_api/apps/baota').glob('test*.py'))]
    subprocess.run(command + ['manage.py', 'test', *modules, 'apps.setting.test_health', 'apps.ops.tests',
        'apps.ops.test_result_counts', 'apps.ops.expiry.tests', 'apps.baota.inventory.tests',
        '--settings=spug.test_settings', '--noinput'], check=True, timeout=300)
    for folder in ('upgrade-review', 'expiry'):
        subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', str(source / 'deploy' / folder),
                        '-p', 'test_*.py'], check=True, timeout=120,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
        '--pids-limit', '32', '-v', str(source / 'deploy/baota/test_image.py') + ':/test_image.py:ro',
        '--entrypoint', 'python3', image_id, '/test_image.py', 'expiry'], check=True, timeout=90)
    current_id = image_identity(json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', image], text=True))[0], revision)
    if current_id != image_id:
        raise ValueError('Image tag changed during isolated verification')
    (stage / 'isolated-checks.json').write_text(json.dumps({'revision': revision, 'previous_revision': previous,
        'image': image, 'image_id': image_id, 'archive_sha256': args.archive_sha256, 'linux_regression': True,
        'release_failure_tests': True, 'image_wiring': True, 'schema_changes': False}))
    print(json.dumps({'revision': revision, 'isolated_checks': 'passed'}))


if __name__ == '__main__':
    main()
