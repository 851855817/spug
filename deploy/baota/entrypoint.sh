#!/bin/bash
set -euo pipefail

# Keep the key outside the source directory that the upstream entrypoint syncs.
python3 - <<'PY'
import hashlib
import os
from pathlib import Path
from cryptography.fernet import Fernet

path = Path(os.environ.get('BAOTA_KEY_FILE', '/data/baota.key'))
marker = Path(str(path) + '.sha256')
if not path.exists():
    if marker.exists():
        raise SystemExit('[baota] Encryption key is missing. Restore the original key; refusing to replace it.')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(Fernet.generate_key())
key = path.read_bytes().strip()
Fernet(key)  # Validate before starting services.
digest = hashlib.sha256(key).hexdigest()
if marker.exists() and marker.read_text().strip() != digest:
    raise SystemExit('[baota] Encryption key fingerprint changed. Restore the original key.')
os.chmod(path, 0o600)
if not marker.exists():
    with marker.open('x') as stream:
        os.chmod(marker, 0o600)
        stream.write(digest + '\n')
print('[baota] Persistent encryption key verified.')
PY

exec /entrypoint.sh "$@"
