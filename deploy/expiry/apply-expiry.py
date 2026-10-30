"""Fixed-path, reversible overlay. Never removes runtime migration history."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

revision, mode = sys.argv[1:]
assert re.fullmatch('[0-9a-f]{40}', revision) and mode in ('publish', 'restore')
source = Path('/tmp/spug-ui-' + revision[:7])
manifest = json.loads((source / 'api-release.json').read_text())
assert manifest['revision'] == revision
files = manifest['sha256']
for name, expected in files.items():
    relative = Path(name)
    assert not relative.is_absolute() and '..' not in relative.parts and 'migrations' not in relative.parts
    assert relative.suffix == '.py' or name == 'apps/baota/data/official_api_catalog.json'
    assert name.startswith(('apps/baota/', 'apps/ops/', 'apps/setting/')) or name in ('spug/settings.py', 'spug/urls.py', 'apps/schedule/scheduler.py')
    assert hashlib.sha256((source / 'source/spug_api' / name).read_bytes()).hexdigest() == expected
roots = [Path('/opt/spug/spug_api'), Path('/data/spug/spug_api')]
backup = Path('/data/spug-api-backups') / revision
if mode == 'publish':
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    originals = {}
    for index, root in enumerate(roots):
        for name in files:
            target = root / name
            assert not target.is_symlink()
            key = str(index) + '/' + name
            originals[key] = None
            if target.exists():
                saved = backup / str(index) / name
                saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                originals[key] = hashlib.sha256(target.read_bytes()).hexdigest()
                assert hashlib.sha256(saved.read_bytes()).hexdigest() == originals[key]
    (backup / 'complete.json').write_text(json.dumps({'revision': revision, 'originals': originals}))

receipt = json.loads((backup / 'complete.json').read_text())
assert receipt['revision'] == revision
originals = receipt['originals']
assert set(originals) == {str(index) + '/' + name for index in range(len(roots)) for name in files}


def verify_backup():
    # Validate the entire backup before restoring or deleting even one file.
    for key, expected in originals.items():
        saved = backup / key
        assert not saved.is_symlink()
        if expected is None:
            assert not saved.exists()
        else:
            assert hashlib.sha256(saved.read_bytes()).hexdigest() == expected

def replace(publish):
    if not publish:
        verify_backup()
    for index, root in enumerate(roots):
        for name in files:
            target = root / name
            origin = source / 'source/spug_api' / name if publish else backup / str(index) / name
            assert not target.is_symlink()
            if not publish and originals[str(index) + '/' + name] is None:
                target.unlink(missing_ok=True)
                assert not target.exists()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('.' + target.name + '.release')
            shutil.copyfile(origin, temporary)
            temporary.chmod(0o644)
            temporary.replace(target)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(origin.read_bytes()).hexdigest()

try:
    replace(mode == 'publish')
except Exception:
    if mode == 'publish':
        replace(False)
    raise
print(json.dumps({'revision': revision, 'mode': mode, 'modules': len(files)}))
