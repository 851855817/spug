"""Verify an immutable patch bundle and run isolated checks; never change live data."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


def unchanged_api_digest(baseline, candidate):
    if not isinstance(baseline, dict) or not 1 <= len(baseline) <= 20000 or candidate != baseline:
        raise ValueError('UI image backend file set or content differs')
    for name, value in baseline.items():
        if not isinstance(name, str) or not name or '\\' in name or ':' in name:
            raise ValueError('Invalid backend snapshot path')
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or path.as_posix() != name or
                not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)):
            raise ValueError('Invalid backend snapshot entry')
    return hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def image_api_snapshot(image_id, probe):
    if not isinstance(image_id, str) or not re.fullmatch('sha256:[0-9a-f]{64}', image_id):
        raise ValueError('Backend snapshot requires an immutable image')
    result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
        '--memory', '128m', '--cpus', '1', '--pids-limit', '32', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '--entrypoint', 'python3', image_id, '-',
        json.dumps({'mode': 'image-api', 'root': '/opt/spug/spug_api'})],
        input=probe, text=True, stdout=subprocess.PIPE, check=True, timeout=90)
    snapshot = json.loads(result.stdout)
    unchanged_api_digest(snapshot, snapshot)
    return snapshot


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
    baseline_id = image_identity(json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', 'spug-baota:' + previous[:7]], text=True))[0], previous)
    subprocess.run(['docker', 'build', '--network', 'none', '--build-arg', 'SOURCE_REVISION=' + revision,
                    '-t', image, str(stage)], check=True, timeout=300)
    image_id = image_identity(json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', image], text=True))[0], revision)
    source = stage / 'source'
    preservation = {}
    if manifest.get('profile') in ('operations-ui', 'task-results-ui', 'workbench-ui', 'branding-ui', 'personal-reminders-ui'):
        # Read the fixed literal without importing staged Python: imports can
        # write unarchived __pycache__ files and invalidate the bundle binding.
        tree = ast.parse((source / 'deploy/upgrade-review/publish.py').read_text())
        definitions = [node.value for node in tree.body if isinstance(node, ast.Assign)
                       and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id == 'FILE_PROBE']
        if len(definitions) != 1:
            raise ValueError('Missing fixed API file probe')
        probe = ast.literal_eval(definitions[0])
        if not isinstance(probe, str) or not probe:
            raise ValueError('Invalid fixed API file probe')
        baseline_api = image_api_snapshot(baseline_id, probe)
        candidate_api = image_api_snapshot(image_id, probe)
        preservation = {'baseline_image_id': baseline_id,
                        'unchanged_api_tree_sha256': unchanged_api_digest(baseline_api, candidate_api)}
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
        '--pids-limit', '32', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', str(stage) + ':/release:ro',
        '--entrypoint', 'python3', image_id, '/release/source/deploy/upgrade-review/check_image.py',
        '/release', revision], check=True, timeout=90)
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
        '--memory', '256m', '--cpus', '1', '--pids-limit', '128', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'PYTHONUNBUFFERED=1', '-v', str(source / 'spug_api') + ':/source/spug_api:ro',
        '--workdir', '/source/spug_api', '--entrypoint', 'python3', image_id]
    modules = ['apps.baota.' + path.stem for path in sorted((source / 'spug_api/apps/baota').glob('test*.py'))]
    subprocess.run(command + ['manage.py', 'test', *modules, 'apps.setting.test_health', 'apps.setting.test_reminder_preferences', 'apps.ops.tests',
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
    if preservation and image_identity(json.loads(subprocess.check_output(
            ['docker', 'image', 'inspect', 'spug-baota:' + previous[:7]], text=True))[0], previous) != baseline_id:
        raise ValueError('Baseline image tag changed during isolated verification')
    (stage / 'isolated-checks.json').write_text(json.dumps({'revision': revision, 'previous_revision': previous,
        'image': image, 'image_id': image_id, 'archive_sha256': args.archive_sha256, 'linux_regression': True,
        'release_failure_tests': True, 'image_wiring': True, 'schema_changes': False, **preservation}))
    print(json.dumps({'revision': revision, 'isolated_checks': 'passed'}))


if __name__ == '__main__':
    main()
