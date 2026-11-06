import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ssl_manifest', Path(__file__).with_name('manifest.py'))
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class ManifestTests(unittest.TestCase):
    def test_fixed_runtime_set_is_accepted(self):
        manifest.validate_changes({'spug_api/' + name for name in manifest.API_FILES} |
                                  {'spug_web/' + name for name in manifest.UI_FILES})

    def test_unreviewed_runtime_change_is_refused(self):
        for name in ('spug_api/spug/settings.py', 'spug_api/apps/account/views.py',
                     'spug_api/apps/monitor/ssl/arbitrary.py', 'spug_web/src/libs/http.js'):
            with self.subTest(name=name), self.assertRaises(manifest.ManifestFailure):
                manifest.validate_changes({'spug_api/' + item for item in manifest.API_FILES} |
                                          {'spug_web/' + item for item in manifest.UI_FILES} | {name})

    def test_missing_required_runtime_is_refused(self):
        with self.assertRaises(manifest.ManifestFailure):
            manifest.validate_changes({'spug_api/' + name for name in manifest.API_FILES[1:]})


if __name__ == '__main__':
    unittest.main()
