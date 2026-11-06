import importlib.util
from pathlib import Path
import json
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ssl_package_tested', Path(__file__).with_name('package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class SourceExportTests(unittest.TestCase):
    def test_source_archive_includes_original_public_assets_for_real_release_checks(self):
        repo = Path(__file__).resolve().parents[2]
        def git(*args):
            self.assertEqual(args[:2], ('ls-files', '-z'))
            names = ('LICENSE', 'spug_web/public/kops-20261027/LICENSE.txt',
                     'spug_web/public/kops-20261027/manifest.json', 'spug_web/public/index.html',
                     '.github/workflows/baota-checks.yml')
            return '\0'.join(name for name in names if any(
                name == prefix or name.startswith(prefix + '/') for prefix in args[2:])).encode()
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            package.export_source(repo, stage, git)
            source = stage / 'source'
            public = source / 'spug_web/public'
            self.assertEqual((source / 'LICENSE').read_text(encoding='utf8'),
                             (public / 'kops-20261027/LICENSE.txt').read_text(encoding='utf8'))
            self.assertIn('/kops-20261027/favicon.svg', (public / 'index.html').read_text(encoding='utf8'))
            self.assertEqual(json.loads((public / 'kops-20261027/manifest.json').read_text(encoding='utf8'))['start_url'], '../')
            self.assertTrue((source / '.github/workflows/baota-checks.yml').is_file())
            self.assertFalse((source / 'spug_web/node_modules').exists())


if __name__ == '__main__': unittest.main()
