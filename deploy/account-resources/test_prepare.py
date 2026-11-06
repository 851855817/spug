import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


class HistoryPreparationTests(unittest.TestCase):
    def module(self):
        path = HERE / 'prepare.py'
        self.assertTrue(path.is_file(), 'Missing history-bound offline migration preparation')
        spec = importlib.util.spec_from_file_location('resource_prepare', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def history(self, root, content=b'from django.db import migrations\nclass Migration(migrations.Migration):\n dependencies=[]\n operations=[]\n'):
        path = root / 'apps/account/migrations/0001_initial.py'
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        return {'account/0001_initial': {'parents': [], 'sha256': hashlib.sha256(content).hexdigest()}}

    def test_copies_only_the_hashed_migration_nodes_and_empty_package_marker(self):
        prepare = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = self.history(root / 'history')
            (root / 'history/settings.py').write_text('sensitive unrelated settings')
            prepare.stage_history(root / 'history', root / 'source', expected)
            self.assertEqual({p.relative_to(root / 'source').as_posix() for p in (root / 'source').rglob('*') if p.is_file()},
                             {'apps/account/migrations/0001_initial.py', 'apps/account/migrations/__init__.py'})
            self.assertEqual((root / 'source/apps/account/migrations/__init__.py').read_bytes(), b'')

    def test_changed_or_unknown_migration_and_data_operations_fail_before_execution(self):
        prepare = self.module()
        for mode in ('changed', 'extra', 'RunPython', 'RunSQL'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                content = ('from django.db import migrations\nclass Migration(migrations.Migration):\n'
                           ' dependencies=[]\n operations=[migrations.%s(print)]\n' % mode).encode() if mode.startswith('Run') else None
                expected = self.history(root / 'history', content) if content else self.history(root / 'history')
                if mode == 'changed': (root / 'history/apps/account/migrations/0001_initial.py').write_text('changed')
                if mode == 'extra': (root / 'history/apps/account/migrations/0002_unreviewed.py').write_text('pass')
                with self.assertRaises(ValueError):
                    prepare.stage_history(root / 'history', root / 'source', expected)
                self.assertFalse((root / 'source').exists(), 'validation must precede output writes')


if __name__ == '__main__':
    unittest.main()
