"""Package committed API/deploy sources and locally built static assets."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

repo = Path(__file__).resolve().parents[2]
output_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo.parent
assert output_dir.is_dir()
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo).strip(), 'Commit and verify sources first'
build = repo / 'spug_web/build'
manifest = json.loads((build / 'asset-manifest.json').read_text())
output = output_dir / ('spug-monitor-' + revision[:7] + '.tar.gz')
archive = subprocess.check_output(['git', 'archive', 'HEAD', 'spug_api', 'deploy'], cwd=repo)
with tarfile.open(fileobj=io.BytesIO(archive)) as source, tarfile.open(output, 'w:gz') as target:
    for member in source.getmembers():
        if member.isfile() and member.name.endswith(('.sh', '.Dockerfile', 'Dockerfile', '.py')):
            # Checkout CRLF must not break the server's shell scripts or image patch.
            value = source.extractfile(member).read().replace(b'\r\n', b'\n')
            member.size = len(value)
            target.addfile(member, io.BytesIO(value))
        else:
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
    target.add(build, arcname='spug_web/build')
metadata = {'revision': revision, 'archive': str(output), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'main_asset': manifest['files']['main.js'], 'css_asset': manifest['files']['main.css']}
(output_dir / 'monitor-release.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
print(json.dumps(metadata))
