"""Freeze the reviewed production-to-candidate diff; never widen by directory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


PROFILE = 'account-resources-v1'
RESOURCE_HELPERS = ('contract.py', 'production-baseline.json', 'scope.py', 'reviewed-scope.json',
                    'host.py', 'schema.py', 'publish.py', 'stage.py', 'rehearsal.py', 'prepare.py',
                    'overlay.py', 'resource-audit.logrotate', 'migration-plan.json', 'image_probe.py')
HELPER_PATHS = tuple('deploy/account-resources/' + name for name in RESOURCE_HELPERS) + (
    'deploy/upgrade-review/publish.py', 'deploy/upgrade-review/publish-ui.py',
    'deploy/upgrade-review/fingerprint.py', 'deploy/inventory/release_safety.py',
) + tuple('deploy/account-resources/migrations/apps/%s/migrations/0002_account_resources.py' % app
          for app in ('account', 'alarm', 'deploy', 'notify'))


def require(value, code):
    if not value: raise ValueError(code)


def runtime_file(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts, 'source-path-invalid')
    if name.startswith('spug_api/'):
        return (path.suffix in ('.py', '.json') and not path.name.startswith('test')
                and not path.name.endswith(('_test_urls.py', '_test_settings.py'))
                and path.name not in ('test_settings.py', 'baota_ci_settings.py')
                and '__pycache__' not in path.parts)
    return (name.startswith(('spug_web/src/', 'spug_web/public/'))
            and '.test.' not in path.name and path.suffix not in ('.md', '.map'))


def validate_names(expected, actual):
    require(set(expected) == set(actual), 'reviewed-runtime-diff-not-exact')


def freeze(repo, reviewed):
    repo = Path(repo)
    def git(*args, input=None):
        return subprocess.check_output(['git', *args], cwd=repo, input=input)
    previous = reviewed['previous_revision']
    require(reviewed['profile'] == PROFILE and re.fullmatch('[0-9a-f]{40}', previous), 'scope-profile-or-baseline-invalid')
    revision = git('rev-parse', 'HEAD').decode().strip()
    require(revision != previous and git('rev-parse', previous + '^{commit}').decode().strip() == previous,
            'scope-revisions-invalid')
    dirty = git('status', '--porcelain', '--untracked-files=all', '--', 'spug_api', 'spug_web/src', 'spug_web/public')
    require(not dirty.strip(), 'candidate-runtime-not-clean')
    rows = git('diff', '--name-status', '--no-renames', previous, revision).decode().splitlines()
    changed = {}
    for row in rows:
        status, name = row.split('\t')
        if not runtime_file(name): continue
        require(status in ('A', 'M'), 'runtime-deletion-or-rename-forbidden')
        changed[name] = status
    validate_names(reviewed['runtime_paths'], changed)
    files = {}
    for name, status in sorted(changed.items()):
        target = repo / name
        require(target.is_file() and not target.is_symlink(), 'candidate-source-linked-or-missing')
        content = git('show', revision + ':' + name).replace(b'\r\n', b'\n')
        require(target.read_bytes().replace(b'\r\n', b'\n') == content, 'candidate-checkout-content-mismatch')
        old = git('show', previous + ':' + name).replace(b'\r\n', b'\n') if status == 'M' else None
        files[name] = {'sha256': hashlib.sha256(content).hexdigest(),
                       'previous_sha256': hashlib.sha256(old).hexdigest() if old is not None else None}
    def source_hashes(ref):
        names = [name for name in git('ls-tree', '-r', '--name-only', ref, '--', 'spug_api').decode().splitlines()
                 if runtime_file(name)]
        requests = [ref + ':' + name for name in names]
        raw = git('cat-file', '--batch', input=('\n'.join(requests) + '\n').encode())
        position, result = 0, {}
        for name in names:
            end = raw.index(b'\n', position)
            header = raw[position:end].split()
            require(len(header) == 3 and header[1] == b'blob', 'scope-git-object-invalid')
            size = int(header[2]); position = end + 1
            content = raw[position:position + size].replace(b'\r\n', b'\n')
            position += size + 1
            result[name[len('spug_api/'):]] = hashlib.sha256(content).hexdigest()
        require(position == len(raw), 'scope-git-object-trailing-data')
        return result
    helper_hashes = {}
    if (repo / 'deploy/account-resources/contract.py').exists():
        require(not git('status', '--porcelain', '--untracked-files=all', '--', *HELPER_PATHS).strip(),
                'release-helpers-not-committed-clean')
        for name in HELPER_PATHS:
            helper_hashes[name] = hashlib.sha256(git('show', revision + ':' + name).replace(b'\r\n', b'\n')).hexdigest()
    return {'profile': PROFILE, 'previous_revision': previous, 'revision': revision, 'files': files,
            'candidate_api_sha256': source_hashes(revision), 'baseline_api_sha256': source_hashes(previous),
            'release_helpers': helper_hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reviewed = json.loads(Path(__file__).with_name('reviewed-scope.json').read_text())
    value = freeze(args.repo, reviewed)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
    print(json.dumps({'profile': PROFILE, 'revision': value['revision'], 'runtime_files': len(value['files'])}))


if __name__ == '__main__':
    main()
