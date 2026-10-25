"""Publish verified static assets without restarting API or task workers."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

revision = sys.argv[1]
mode = sys.argv[2]
assert re.fullmatch(r'[0-9a-f]{40}', revision) and mode in ('publish', 'restore')
stage = Path('/tmp/spug-ui-' + revision[:7]) / 'build'
release = json.loads((stage / 'ui-release.json').read_text())
assert release['revision'] == revision
files = []
for name, expected in release['sha256'].items():
    relative = Path(name)
    assert not relative.is_absolute() and '..' not in relative.parts
    source = stage / relative
    assert source.is_file() and not source.is_symlink()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, name
    files.append(relative)
assert Path('index.html') in files and Path('asset-manifest.json') in files
files.append(Path('ui-release.json'))
backup = Path('/data/spug-ui-backups') / revision
roots = [Path('/opt/spug/spug_web/build'), Path('/data/spug/spug_web/build')]
entries = ('index.html', 'asset-manifest.json', 'ui-release.json')
if mode == 'publish':
    backup.mkdir(parents=True, exist_ok=False)
    os.chmod(backup, 0o700)
    originals = {}
    for number, root in enumerate(roots):
        assert root.is_dir() and not root.is_symlink()
        saved = backup / str(number)
        saved.mkdir()
        for name in entries:
            assert not (root / name).is_symlink()
            key = str(number) + '/' + name
            originals[key] = None
            if (root / name).is_file():
                shutil.copy2(root / name, saved / name)
                originals[key] = hashlib.sha256((root / name).read_bytes()).hexdigest()
                assert hashlib.sha256((saved / name).read_bytes()).hexdigest() == originals[key]
    (backup / 'complete.json').write_text(json.dumps({'revision': revision, 'originals': originals}))


def restore():
    receipt = json.loads((backup / 'complete.json').read_text())
    assert receipt['revision'] == revision
    originals = receipt['originals']
    assert set(originals) == {str(i) + '/' + name for i in range(len(roots)) for name in entries}
    for key, expected in originals.items():
        saved = backup / key
        assert not saved.is_symlink()
        if expected is None:
            assert not saved.exists()
        else:
            assert hashlib.sha256(saved.read_bytes()).hexdigest() == expected
    for number, root in enumerate(roots):
        assert root.is_dir() and not root.is_symlink()
        for name in entries:
            destination = root / name
            assert not destination.is_symlink()
            expected = originals[str(number) + '/' + name]
            if expected is None:
                destination.unlink(missing_ok=True)
                assert not destination.exists()
            else:
                temporary = root / ('.' + name + '.rollback')
                shutil.copyfile(backup / str(number) / name, temporary)
                os.replace(temporary, destination)
                assert hashlib.sha256(destination.read_bytes()).hexdigest() == expected

try:
    for root in roots if mode == 'publish' else ():
        # Keep old hashed assets for pages already open. Publish index last.
        for relative in sorted(files, key=lambda path: path == Path('index.html')):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            assert not destination.is_symlink()
            temporary = destination.with_name('.' + destination.name + '.' + revision[:7] + '.tmp')
            shutil.copyfile(stage / relative, temporary)
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
        for name, expected in release['sha256'].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    if mode == 'restore':
        restore()
except Exception:
    if mode == 'publish':
        restore()
    raise
print(json.dumps({'revision': revision, 'mode': mode, 'verified_files': len(files),
                  'updated_startup_source_and_live_assets': True, 'backup': str(backup)}))
