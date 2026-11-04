"""Package a clean SSL feature checkout, without production data or settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from manifest import API_FILES, validate_changes


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('previous_revision')
    args.add_argument('--node', required=True)
    options = args.parse_args()
    repo = Path(__file__).resolve().parents[2]
    def git(*values):
        return subprocess.check_output(['git', *values], cwd=repo)
    revision = git('rev-parse', 'HEAD').decode().strip()
    previous = git('rev-parse', '--verify', options.previous_revision + '^{commit}').decode().strip()
    assert not git('status', '--porcelain').strip(), 'Clean checkout required'
    validate_changes(git('diff', '--name-only', previous, revision).decode().splitlines())
    preload = str(repo / 'deploy/upgrade-review/build-limit.cjs')
    environment = dict(os.environ, NODE_OPTIONS='--require=' + json.dumps(preload) +
        ' --openssl-legacy-provider --max-old-space-size=768 --max-semi-space-size=8',
        GENERATE_SOURCEMAP='false', CI='false', REACT_APP_RELEASE_REVISION=revision)
    subprocess.run([options.node, '--require', preload, 'node_modules/react-app-rewired/bin/index.js', 'build'],
                   cwd=repo / 'spug_web', env=environment, check=True, timeout=600)
    assert git('rev-parse', 'HEAD').decode().strip() == revision and not git('status', '--porcelain').strip()
    stage = repo.parent / ('ssl-' + revision[:7])
    stage.mkdir(exist_ok=False)
    shutil.copytree(repo / 'spug_web/build', stage / 'build')
    for name in filter(None, git('ls-files', '-z', 'spug_api', 'deploy', 'LICENSE',
                                '.github/workflows/baota-checks.yml').decode().split('\0')):
        source = repo / name
        assert source.is_file() and not source.is_symlink()
        target = stage / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        if target.suffix in ('.py', '.ini', '.sh', '.service', '.timer'):
            content = content.replace(b'\r\n', b'\n')
        target.write_bytes(content)
    ui = {p.relative_to(stage / 'build').as_posix(): sha(p) for p in (stage / 'build').rglob('*')
          if p.is_file() and p.name != 'ui-release.json'}
    (stage / 'build/ui-release.json').write_text(json.dumps({'revision': revision, 'sha256': ui}, indent=2))
    hashes = {name: sha(stage / 'source/spug_api' / name) for name in API_FILES}
    old = {}
    for name in API_FILES:
        exists = git('ls-tree', '--name-only', previous, '--', 'spug_api/' + name).strip()
        old[name] = hashlib.sha256(git('show', previous + ':spug_api/' + name).replace(b'\r\n', b'\n')).hexdigest() if exists else None
    manifest = {'revision': revision, 'previous_revision': previous, 'schema_changes': True,
                'api_sha256': hashes, 'previous_api_sha256': old, 'ui_sha256': ui,
                'ui_release_sha256': sha(stage / 'build/ui-release.json')}
    (stage / 'release.json').write_text(json.dumps(manifest, indent=2))
    archive = stage.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as output:
        output.add(stage, arcname=stage.name)
    result = {'revision': revision, 'previous_revision': previous, 'archive': str(archive), 'sha256': sha(archive)}
    (repo.parent / 'ssl-release.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
