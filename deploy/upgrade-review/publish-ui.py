"""Reversible static release, including CRA's mutable service-worker entrypoint."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys


ENTRIES = ('index.html', 'asset-manifest.json', 'ui-release.json', 'service-worker.js')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(path):
    state = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            state.update(block)
    return state.hexdigest()


def unlinked(path):
    return not path.is_symlink() and all(not parent.is_symlink() for parent in path.parents)


def atomic_copy(source, destination, suffix, mode):
    require(unlinked(destination), 'linked-ui-destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + destination.name + suffix)
    require(not temporary.exists() and unlinked(temporary), 'ui-temporary-already-exists')
    try:
        with temporary.open('xb') as output, source.open('rb') as original:
            temporary.chmod(mode)
            shutil.copyfileobj(original, output)
        os.replace(temporary, destination)
        require(sha(source) == sha(destination), 'ui-copy-hash-mismatch')
    finally:
        temporary.unlink(missing_ok=True)


def publish_ui(revision, mode, *, stage=None, roots=None, backup=None):
    """Inject paths for local failure tests; CLI uses only fixed runtime paths."""
    require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision) and
            mode in ('publish', 'restore'), 'invalid-ui-release-arguments')
    stage = Path('/tmp/spug-ui-' + revision[:7]) / 'build' if stage is None else Path(stage)
    roots = [Path('/opt/spug/spug_web/build'), Path('/data/spug/spug_web/build')] if roots is None else [Path(root) for root in roots]
    backup = Path('/data/spug-ui-backups') / revision if backup is None else Path(backup)
    require(len(roots) == 2 and len(set(roots)) == 2 and stage.is_dir() and unlinked(stage),
            'invalid-ui-release-paths')
    require(all(root.is_dir() and unlinked(root) for root in roots), 'invalid-ui-runtime-root')
    release_path = stage / 'ui-release.json'
    require(release_path.is_file() and unlinked(release_path), 'missing-ui-manifest')
    release = json.loads(release_path.read_text())
    require(release['revision'] == revision and isinstance(release['sha256'], dict), 'ui-revision-mismatch')
    files = []
    for name, expected in release['sha256'].items():
        require(isinstance(name, str) and '\\' not in name and ':' not in name,
                'unsafe-ui-source-path')
        relative = PurePosixPath(name)
        require(name and not relative.is_absolute() and '..' not in relative.parts and
                name == relative.as_posix() and name != 'ui-release.json', 'unsafe-ui-source-path')
        source = stage.joinpath(*relative.parts)
        require(source.is_file() and unlinked(source) and isinstance(expected, str) and
                re.fullmatch('[0-9a-f]{64}', expected) and sha(source) == expected,
                'ui-source-hash-mismatch')
        files.append(Path(*relative.parts))
    require({Path('index.html'), Path('asset-manifest.json')} <= set(files), 'ui-entrypoints-missing')
    files.append(Path('ui-release.json'))
    require(unlinked(backup), 'linked-ui-backup')

    if mode == 'publish':
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        backup.chmod(0o700)
        originals = {}
        for index, root in enumerate(roots):
            saved = backup / str(index)
            saved.mkdir(mode=0o700)
            for name in ENTRIES:
                target = root / name
                require(unlinked(target) and (not target.exists() or target.is_file()),
                        'invalid-original-ui-entrypoint')
                key = str(index) + '/' + name
                originals[key] = None
                if target.is_file():
                    originals[key] = sha(target)
                    atomic_copy(target, saved / name, '.backup', 0o600)
                    require(sha(saved / name) == originals[key], 'ui-backup-hash-mismatch')
        receipt = backup / 'complete.json'
        with receipt.open('x') as stream:
            receipt.chmod(0o600)
            json.dump({'revision': revision, 'originals': originals}, stream, sort_keys=True)

    def checked_originals():
        require(backup.is_dir() and unlinked(backup), 'invalid-ui-backup-root')
        receipt = backup / 'complete.json'
        require(receipt.is_file() and unlinked(receipt), 'missing-ui-backup-receipt')
        value = json.loads(receipt.read_text())
        require(value['revision'] == revision and isinstance(value['originals'], dict),
                'invalid-ui-backup-receipt')
        originals = value['originals']
        require(set(originals) == {str(index) + '/' + name for index in range(2) for name in ENTRIES},
                'incomplete-ui-backup-receipt')
        # Validate every saved entry and every destination before the first
        # restoration write; a damaged fourth entry cannot restore the others.
        for key, expected in originals.items():
            saved = backup / key
            require(unlinked(saved), 'linked-ui-saved-entrypoint')
            if expected is None:
                require(not saved.exists(), 'unexpected-ui-saved-entrypoint')
            else:
                require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected) and
                        saved.is_file() and sha(saved) == expected, 'ui-saved-hash-mismatch')
        for root in roots:
            require(root.is_dir() and unlinked(root), 'invalid-ui-runtime-root')
            require(all(unlinked(root / name) and
                        (not (root / name).exists() or (root / name).is_file()) for name in ENTRIES),
                    'invalid-ui-restore-destination')
        return originals

    def restore():
        originals = checked_originals()
        for index, root in enumerate(roots):
            for name in ENTRIES:
                destination = root / name
                expected = originals[str(index) + '/' + name]
                if expected is None:
                    destination.unlink(missing_ok=True)
                    require(not destination.exists(), 'ui-absent-entrypoint-not-removed')
                else:
                    atomic_copy(backup / str(index) / name, destination, '.rollback', 0o644)
                    require(sha(destination) == expected, 'ui-restored-hash-mismatch')

    # A complete receipt for both roots/four entries is required before any
    # publication write. Missing service-worker.js is recorded explicitly.
    checked_originals()
    try:
        if mode == 'publish':
            for root in roots:
                # Keep immutable old assets for pages already open; index last.
                for relative in sorted(files, key=lambda path: path == Path('index.html')):
                    atomic_copy(stage / relative, root / relative, '.' + revision[:7] + '.tmp', 0o644)
                for name, expected in release['sha256'].items():
                    require(sha(root / name) == expected, 'ui-published-hash-mismatch')
                require(sha(root / 'ui-release.json') == sha(release_path), 'ui-manifest-copy-mismatch')
        else:
            restore()
    except Exception:
        if mode == 'publish':
            restore()
        raise
    return {'revision': revision, 'mode': mode, 'verified_files': len(files),
            'updated_startup_source_and_live_assets': True, 'backup': str(backup)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        return 2
    try:
        result = publish_ui(*argv)
    except Exception:
        print(json.dumps({'verified': False, 'failure': 'ui-publication-or-restoration-unverified'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
