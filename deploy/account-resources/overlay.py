"""Exact resource-only API overlay; compatibility restore retains migration history."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

MIGRATIONS = {'apps/%s/migrations/0002_account_resources.py' % app for app in ('account', 'alarm', 'deploy', 'notify')}


def approved_paths():
    value = json.loads(Path(__file__).with_name('reviewed-scope.json').read_text())
    require(value['profile'] == 'account-resources-v1', 'overlay-scope-profile-invalid')
    return {name[len('spug_api/'):] for name in value['runtime_paths'] if name.startswith('spug_api/')}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def unlinked(path):
    return not path.is_symlink() and all(not parent.is_symlink() for parent in path.parents)


def sha(path):
    require(path.is_file() and unlinked(path), 'overlay-file-missing-or-linked')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(manifest, mode, source, roots, backup, approved=None):
    require(mode in ('publish', 'restore', 'restore-compat') and len(roots) == 2
            and len(set(roots)) == 2, 'overlay-arguments-invalid')
    require(all(root.is_dir() and unlinked(root) for root in roots) and unlinked(backup)
            and source.is_dir() and unlinked(source), 'overlay-path-invalid')
    revision, files = manifest['revision'], manifest['sha256']
    require(manifest.get('profile') == 'account-resources-v1' and re.fullmatch(r'[0-9a-f]{40}', revision),
            'overlay-profile-or-revision-invalid')
    allowed = set(approved_paths() if approved is None else approved) | MIGRATIONS
    require(isinstance(files, dict) and set(files) == allowed, 'overlay-file-set-not-exact')
    for name, expected in files.items():
        require(PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts,
                'overlay-relative-path-invalid')
        require(sha(source / name) == expected and all(unlinked(root / name) for root in roots),
                'overlay-source-or-target-invalid')
    if mode == 'publish':
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        originals = {}
        for index, root in enumerate(roots):
            for name in files:
                target = root / name
                key = str(index) + '/' + name
                originals[key] = sha(target) if target.exists() else None
                if target.exists():
                    saved = backup / key
                    saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                    require(sha(saved) == originals[key], 'overlay-backup-invalid')
        receipt = backup / 'complete.json'
        receipt.write_text(json.dumps({'revision': revision, 'originals': originals}))
        receipt.chmod(0o600)
    receipt_path = backup / 'complete.json'
    require(unlinked(receipt_path), 'overlay-receipt-linked')
    receipt = json.loads(receipt_path.read_text())
    originals = receipt['originals']
    require(receipt['revision'] == revision and set(originals) == {
        str(index) + '/' + name for index in range(2) for name in files}, 'overlay-receipt-invalid')
    # Validate every original before the first write or deletion during restore.
    for key, expected in originals.items():
        saved = backup / key
        require(unlinked(saved) and (not saved.exists() if expected is None else sha(saved) == expected),
                'overlay-original-backup-corrupt')
    for index, root in enumerate(roots):
        for name in files:
            keep = mode == 'publish' or mode == 'restore-compat' and name in MIGRATIONS
            target = root / name
            if not keep and originals[str(index) + '/' + name] is None:
                target.unlink(missing_ok=True)
                require(not target.exists(), 'overlay-remove-failed')
                continue
            origin = source / name if keep else backup / str(index) / name
            require(unlinked(target), 'overlay-target-linked')
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name('.' + target.name + '.resource-release')
            require(not temp.exists() and unlinked(temp), 'overlay-temporary-exists')
            try:
                with temp.open('xb') as output, origin.open('rb') as stream:
                    temp.chmod(0o644)
                    shutil.copyfileobj(stream, output)
                temp.replace(target)
                require(sha(target) == sha(origin), 'overlay-result-invalid')
            finally:
                temp.unlink(missing_ok=True)
    return {'revision': revision, 'mode': mode, 'modules': len(files)}


if __name__ == '__main__':
    revision, mode = sys.argv[1:]
    require(re.fullmatch(r'[0-9a-f]{40}', revision), 'overlay-revision-invalid')
    stage = Path('/tmp/spug-ui-' + revision[:7])
    print(json.dumps(apply(json.loads((stage / 'api-release.json').read_text()), mode,
        stage / 'source/spug_api', [Path('/opt/spug/spug_api'), Path('/data/spug/spug_api')],
        Path('/data/spug-api-backups') / revision)))
