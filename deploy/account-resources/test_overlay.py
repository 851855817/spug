import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


class OverlayTests(unittest.TestCase):
    def module(self):
        path = HERE / 'overlay.py'
        self.assertTrue(path.is_file(), 'Missing resource-specific dual-root overlay')
        spec = importlib.util.spec_from_file_location('resource_overlay', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, directory):
        root = Path(directory)
        source, backup = root / 'source', root / 'backup'
        roots = [root / 'opt', root / 'data']
        names = ['apps/account/access.py', 'libs/resource_logging.py']
        files = names + ['apps/%s/migrations/0002_account_resources.py' % app for app in ('account', 'alarm', 'deploy', 'notify')]
        for name in files:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('new-' + name)
        for index, runtime in enumerate(roots):
            path = runtime / names[0]
            path.parent.mkdir(parents=True)
            path.write_text('old-%d' % index)
        manifest = {'profile': 'account-resources-v1', 'revision': 'a' * 40,
                    'sha256': {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files}}
        return source, roots, backup, names, manifest

    def test_compatibility_restore_keeps_only_four_migrations_and_original_receipt(self):
        overlay = self.module()
        with tempfile.TemporaryDirectory() as directory:
            source, roots, backup, names, manifest = self.fixture(directory)
            overlay.apply(manifest, 'publish', source, roots, backup, approved=names)
            original = (backup / 'complete.json').read_bytes()
            overlay.apply(manifest, 'restore-compat', source, roots, backup, approved=names)
            self.assertEqual((backup / 'complete.json').read_bytes(), original)
            for index, root in enumerate(roots):
                self.assertEqual((root / names[0]).read_text(), 'old-%d' % index)
                self.assertFalse((root / names[1]).exists())
                for app in ('account', 'alarm', 'deploy', 'notify'):
                    self.assertTrue((root / ('apps/%s/migrations/0002_account_resources.py' % app)).is_file())

    def test_corrupt_receipt_refuses_every_restore_write(self):
        overlay = self.module()
        with tempfile.TemporaryDirectory() as directory:
            source, roots, backup, names, manifest = self.fixture(directory)
            overlay.apply(manifest, 'publish', source, roots, backup, approved=names)
            (backup / '0' / names[0]).write_text('tampered')
            with self.assertRaises(ValueError):
                overlay.apply(manifest, 'restore-compat', source, roots, backup, approved=names)
            self.assertTrue(all((root / names[0]).read_text().startswith('new-') for root in roots))

    def test_wrong_profile_subset_extra_file_and_missing_source_fail_before_backup(self):
        overlay = self.module()
        for mode in ('profile', 'subset', 'extra', 'source'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                source, roots, backup, names, manifest = self.fixture(directory)
                if mode == 'profile': manifest['profile'] = 'ssl-monitor'
                if mode == 'subset': manifest['sha256'].pop(names[1])
                if mode == 'extra': manifest['sha256']['apps/account/views.py'] = 'b' * 64
                if mode == 'source': (source / names[1]).unlink()
                with self.assertRaises(ValueError):
                    overlay.apply(manifest, 'publish', source, roots, backup, approved=names)
                self.assertFalse(backup.exists())


if __name__ == '__main__': unittest.main()
