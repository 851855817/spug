"""Package a clean reviewed checkout and built UI; never include local configuration."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


repo = Path(__file__).resolve().parents[2]
work = repo.parent
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo).strip()
stage = work / ('expiry-' + revision[:7])
stage.mkdir(exist_ok=False)
shutil.copytree(repo / 'spug_web/build', stage / 'build')
tracked = subprocess.check_output(['git', 'ls-files', 'spug_api', 'deploy'], cwd=repo, text=True).splitlines()
for name in tracked:
    destination = stage / 'source' / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = (repo / name).read_bytes()
    if destination.suffix in ('.py', '.ini', '.sh', '.service', '.timer'):
        content = content.replace(b'\r\n', b'\n')
    destination.write_bytes(content)
api_files = [name[len('spug_api/'):] for name in tracked if name.startswith((
    'spug_api/apps/baota/', 'spug_api/apps/ops/', 'spug_api/apps/setting/'))]
api_files += ['spug/settings.py', 'spug/urls.py', 'apps/schedule/scheduler.py']
assert not any('/migrations/' in name for name in api_files)
hashes = {name: hashlib.sha256((stage / 'source/spug_api' / name).read_bytes()).hexdigest() for name in api_files}
(stage / 'api-release.json').write_text(json.dumps({'revision': revision, 'sha256': hashes}, indent=2))
for original, target in [('apply-expiry.py', 'apply-expiry.py'), ('publish-ui.py', 'deploy-compact-ui.py')]:
    (stage / target).write_bytes((repo / 'deploy/expiry' / original).read_bytes().replace(b'\r\n', b'\n'))
(stage / 'Dockerfile').write_text('''FROM spug-baota:8a64ca1
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
COPY source/spug_api/apps/baota/ /opt/spug/spug_api/apps/baota/
COPY source/spug_api/apps/setting/ /opt/spug/spug_api/apps/setting/
COPY source/spug_api/apps/ops/ /opt/spug/spug_api/apps/ops/
COPY source/spug_api/apps/schedule/scheduler.py /opt/spug/spug_api/apps/schedule/scheduler.py
COPY source/spug_api/spug/settings.py source/spug_api/spug/urls.py /opt/spug/spug_api/spug/
COPY build/ /opt/spug/spug_web/build/
''')
ui_hashes = {path.relative_to(stage / 'build').as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((stage / 'build').rglob('*')) if path.is_file() and path.name != 'ui-release.json'}
(stage / 'build/ui-release.json').write_text(json.dumps({'revision': revision, 'sha256': ui_hashes}, indent=2))
archive = work / (stage.name + '.tar.gz')
with tarfile.open(archive, 'w:gz') as output:
    output.add(stage, arcname=stage.name)
report = {'revision': revision, 'archive': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
(work / 'expiry-release.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report))
