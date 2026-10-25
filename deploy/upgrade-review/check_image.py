"""Verify the prepared image's runtime and UI against the immutable bundle."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys


def sha(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('Missing or linked image file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(root, name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
            or ':' in name or path.as_posix() != name):
        raise ValueError('Invalid image manifest path')
    result = root.joinpath(*path.parts)
    if any(parent.is_symlink() for parent in result.parents):
        raise ValueError('Linked image directory')
    return result


def verify(root, stage, revision):
    release = json.loads((stage / 'release.json').read_text())
    if release['revision'] != revision or release['schema_changes'] is not False:
        raise ValueError('Image revision or scope differs')
    if set(release['api_sha256']) != {'apps/baota/operations/php_update.py'}:
        raise ValueError('Image API overlay differs')
    ui = json.loads((stage / 'build/ui-release.json').read_text())
    if ui['revision'] != revision or not {'index.html', 'asset-manifest.json'} <= set(ui['sha256']):
        raise ValueError('Image UI manifest differs')
    expectations = [(root / 'spug_api', release['api_sha256']),
                    (root / 'spug_web/build', dict(ui['sha256'], **{
                        'ui-release.json': sha(stage / 'build/ui-release.json')}))]
    for directory, hashes in expectations:
        for name, expected in hashes.items():
            if sha(relative(directory, name)) != expected:
                raise ValueError('Prepared image content differs')


if __name__ == '__main__':
    verify(Path('/opt/spug'), Path(sys.argv[1]), sys.argv[2])
    print(json.dumps({'revision': sys.argv[2], 'image_content': 'verified'}))
