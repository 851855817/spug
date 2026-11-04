import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ssl_prepare_tested', Path(__file__).with_name('prepare.py'))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CompatibilityFixtureTests(unittest.TestCase):
    def test_adds_only_isolated_settings_to_exported_rollback_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source', root / 'rollback'
            for folder in (source, target): (folder / 'spug').mkdir(parents=True)
            (source / 'spug/settings.py').write_text('new-production-config')
            (target / 'spug/settings.py').write_text('preserved-rollback-config')
            for name in ('test_settings.py', 'baota_ci_settings.py'):
                (source / 'spug' / name).write_text('isolated-' + name)
            prepare.add_isolated_settings(source, target)
            self.assertEqual((target / 'spug/settings.py').read_text(), 'preserved-rollback-config')
            self.assertEqual({p.name for p in (target / 'spug').iterdir()},
                             {'settings.py', 'test_settings.py', 'baota_ci_settings.py'})
            for name in ('test_settings.py', 'baota_ci_settings.py'):
                self.assertEqual((target / 'spug' / name).read_bytes(), (source / 'spug' / name).read_bytes())


if __name__ == '__main__': unittest.main()
