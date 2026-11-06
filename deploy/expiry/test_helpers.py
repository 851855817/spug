"""Exercise backup completeness and verified restoration against temporary files."""
import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class HelperTests(unittest.TestCase):
    def invoke(self, helper, mode, roots, stage, backup):
        path = Path(__file__).with_name(helper)
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        replacements = {'roots': '_roots', 'backup': '_backup',
                        'source' if helper == 'apply-expiry.py' else 'stage': '_stage'}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in replacements:
                    node.value = ast.Name(id=replacements[name], ctx=ast.Load())
        ast.fix_missing_locations(tree)
        with patch.object(sys, 'argv', [str(path), 'a' * 40, mode]), redirect_stdout(io.StringIO()):
            exec(compile(tree, str(path), 'exec'), {'_roots': roots, '_stage': stage, '_backup': backup})

    def fixture(self, directory, api):
        root = Path(directory)
        roots = [root / 'startup', root / 'live']
        for folder in roots:
            folder.mkdir()
        stage = root / 'stage'; stage.mkdir()
        names = ['apps/ops/models.py', 'apps/ops/expiry/models.py'] if api else ['index.html', 'asset-manifest.json']
        content = {}
        for name in names:
            destination = stage / ('source/spug_api' if api else '') / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text('new-' + name)
            content[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
        (stage / ('api-release.json' if api else 'ui-release.json')).write_text(
            json.dumps({'revision': 'a' * 40, 'sha256': content}))
        for folder in roots:
            target = folder / names[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('old-original')
        return roots, stage, root / 'backup', names

    def test_api_incomplete_backup_cannot_delete_unbacked_originals(self):
        with TemporaryDirectory() as directory:
            roots, stage, backup, names = self.fixture(directory, True)
            with patch('shutil.copy2', side_effect=OSError('injected backup write failure')):
                with self.assertRaises(OSError):
                    self.invoke('apply-expiry.py', 'publish', roots, stage, backup)
            with self.assertRaises((AssertionError, FileNotFoundError)):
                self.invoke('apply-expiry.py', 'restore', roots, stage, backup)
            for root in roots:
                self.assertEqual((root / names[0]).read_text(), 'old-original')

    def test_api_corrupted_backup_refuses_restoration_before_any_mutation(self):
        with TemporaryDirectory() as directory:
            roots, stage, backup, names = self.fixture(directory, True)
            self.invoke('apply-expiry.py', 'publish', roots, stage, backup)
            (backup / '1' / names[0]).write_text('corrupted')
            with self.assertRaises(AssertionError):
                self.invoke('apply-expiry.py', 'restore', roots, stage, backup)
            for root in roots:
                self.assertEqual((root / names[0]).read_text(), 'new-' + names[0])

    def test_ui_explicit_restore_recovers_entries_and_removes_previously_absent_entries(self):
        with TemporaryDirectory() as directory:
            roots, stage, backup, names = self.fixture(directory, False)
            self.invoke('publish-ui.py', 'publish', roots, stage, backup)
            self.invoke('publish-ui.py', 'restore', roots, stage, backup)
            for root in roots:
                self.assertEqual((root / names[0]).read_text(), 'old-original')
                self.assertFalse((root / names[1]).exists())
                self.assertFalse((root / 'ui-release.json').exists())


if __name__ == '__main__':
    unittest.main()
