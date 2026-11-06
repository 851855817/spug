"""Read-only facts from an installed image; never start services or use a DB."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import sys


def scan(root):
    result = {}
    assert root.is_dir() and not root.is_symlink()
    for directory, folders, names in os.walk(root):
        base = Path(directory)
        assert all(not (base / name).is_symlink() for name in folders + names)
        folders[:] = sorted(name for name in folders if name != '__pycache__'
                            and not (base == root and name == 'logs'))
        for name in sorted(names):
            if name.endswith(('.pyc', '.pyo')) or (base == root and name == 'supervisord.log'): continue
            path = base / name
            assert path.is_file()
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    mode, image_id = sys.argv[1:]
    assert mode in ('baseline', 'candidate', 'rollback') and image_id.startswith('sha256:') and len(image_id) == 71
    root = Path('/opt/spug/spug_api')
    result = {'image_id': image_id, 'api': scan(root)}
    if mode != 'baseline':
        config = configparser.ConfigParser(interpolation=None)
        assert config.read('/etc/supervisord.conf') == ['/etc/supervisord.conf']
        result['programs'] = {name[len('program:'):]: {
            'autostart': config.getboolean(name, 'autostart', fallback=True),
            'autorestart': config.getboolean(name, 'autorestart', fallback=True)}
            for name in config.sections() if name.startswith('program:')}
        startup = Path('/entrypoint.sh').read_text().splitlines()
        result['entrypoint'] = {'updatedb': startup.count('python3 manage.py updatedb'),
                                'migrate': startup.count('python3 manage.py migrate --noinput'),
                                'hold': startup.count(': account-resource-maintenance-migrations-held')}
        sys.path.insert(0, str(root))
        os.environ['DJANGO_SETTINGS_MODULE'] = 'spug.settings'
        from django.conf import settings
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
        settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
        settings.CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
        import django
        django.setup()
        from django.urls import get_resolver
        assert not get_resolver('spug.urls').check()
        import spug.asgi
        assert spug.asgi.__file__.startswith(str(root) + '/')
        if mode == 'candidate':
            from django.apps import apps
            from apps.account.resource_migration import ADDITIVE_MODELS
            import libs.resource_logging
            assert libs.resource_logging.__file__.startswith(str(root) + '/')
            result['models'] = sorted(apps.get_model(app, name)._meta.db_table for app, name in ADDITIVE_MODELS)
            result['audit_path'] = os.environ.get('KOPS_RESOURCE_AUDIT_LOG')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': main()
