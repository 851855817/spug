import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ssl_overlay', Path(__file__).with_name('overlay.py'))
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class OverlayTests(unittest.TestCase):
    def test_compatibility_restore_keeps_only_models_and_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            runtimes = [root / 'api0', root / 'api1']
            backup = root / 'backup'
            for directory in [source, *runtimes]: directory.mkdir()
            files = {'apps/monitor/models.py': b'new-registry', 'apps/monitor/ssl/models.py': b'new-models',
                     'apps/monitor/ssl/__init__.py': b'', 'apps/monitor/ssl/jobs.py': b'new-executor',
                     'apps/monitor/migrations/0002_ssl_monitor.py': b'new-migration'}
            for name, content in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            for runtime in runtimes:
                old = runtime / 'apps/monitor/models.py'
                old.parent.mkdir(parents=True)
                old.write_bytes(b'old-registry')
            hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
            receipt = {'revision': 'a' * 40, 'sha256': hashes, 'migration': '0002_ssl_monitor'}
            overlay.apply(receipt, 'publish', source, runtimes, backup)
            overlay.apply(receipt, 'restore-compat', source, runtimes, backup)
            for runtime in runtimes:
                self.assertEqual((runtime / 'apps/monitor/models.py').read_bytes(), b'new-registry')
                self.assertTrue((runtime / 'apps/monitor/migrations/0002_ssl_monitor.py').is_file())
                self.assertFalse((runtime / 'apps/monitor/ssl/jobs.py').exists())

    def test_corrupt_backup_refuses_any_restore_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, backup = root / 'src', root / 'backup'
            source.mkdir()
            runtimes = [root / 'a', root / 'b']
            name = 'apps/monitor/models.py'
            for runtime in runtimes:
                target = runtime / name
                target.parent.mkdir(parents=True)
                target.write_bytes(b'old')
            path = source / name
            path.parent.mkdir(parents=True)
            path.write_bytes(b'new')
            receipt = {'revision': 'b' * 40, 'sha256': {name: hashlib.sha256(b'new').hexdigest()}, 'migration': '0002_ssl_monitor'}
            overlay.apply(receipt, 'publish', source, runtimes, backup)
            (backup / '0' / name).write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                overlay.apply(receipt, 'restore', source, runtimes, backup)
            self.assertTrue(all((runtime / name).read_bytes() == b'new' for runtime in runtimes))

    def test_path_escape_and_unreviewed_source_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('../secret.py', 'apps/account/views.py', 'apps/monitor/migrations/0001_initial.py'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    overlay.apply({'revision': 'a' * 40, 'sha256': {name: 'a' * 64},
                                   'migration': '0002_ssl_monitor'}, 'publish', root, [root / 'a', root / 'b'], root / 'backup')


if __name__ == '__main__': unittest.main()
