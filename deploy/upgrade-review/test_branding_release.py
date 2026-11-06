"""Branding is a UI-only release with public, version-bound AGPL notices."""
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).parent
PROFILE = 'branding-ui'
UI = {'spug_web/src/branding/Brand.js', 'spug_web/src/branding/LegalNotice.js',
      'spug_web/src/branding/source.js', 'spug_web/src/branding/branding.module.css',
    'spug_web/src/pages/ssh/index.js', 'spug_web/src/pages/ssh/Setting.js',
      'spug_web/src/layout/Sider.js', 'spug_web/src/layout/Header.js', 'spug_web/src/layout/Footer.js',
      'spug_web/src/pages/login/index.js', 'spug_web/src/pages/login/login.module.css',
      'spug_web/src/pages/system/setting/About.js', 'spug_web/public/index.html',
      'spug_web/public/kops-20261027/favicon.svg', 'spug_web/public/kops-20261027/manifest.json',
      'spug_web/public/kops-20261027/LICENSE.txt'}


def load(name):
    spec = importlib.util.spec_from_file_location('branding_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class BrandingReleaseTests(unittest.TestCase):
    def test_exact_ui_scope_refuses_backend_extra_and_missing_changes(self):
        package = load('package')
        package.validate_runtime_changes(sorted(UI) + ['README.md', 'MODIFICATIONS.md',
            'spug_web/src/branding/branding.test.js'], PROFILE)
        for names in ([], sorted(UI)[1:], sorted(UI) + ['spug_web/src/routes.js'],
                      sorted(UI) + ['spug_api/apps/baota/views.py']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package.validate_runtime_changes(names, PROFILE)

    def test_same_preservation_and_failure_contract_as_other_ui_profiles(self):
        # Exercise the existing independent manifest/image/rollback contracts under this profile.
        fixture = load('test_workbench_release')
        fixture.PROFILE = PROFILE
        case = fixture.WorkbenchReleaseTests()
        case.test_manifest_and_image_preserve_both_api_modules_with_no_overlay()
        case.test_loading_stage_requires_complete_image_api_preservation_receipt()
        case.test_success_and_partial_failures_never_apply_api_and_use_new_paired_backups()

    def test_static_notices_are_original_license_and_assets_do_not_replace_old_mutable_paths(self):
        repo = HERE.resolve().parents[1]
        public = repo / 'spug_web/public'
        self.assertEqual((repo / 'LICENSE').read_text(encoding='utf-8'), (public / 'kops-20261027/LICENSE.txt').read_text(encoding='utf-8'))
        entry = (public / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<title>KOps · 云枢</title>', entry)
        self.assertIn('/kops-20261027/favicon.svg', entry)
        self.assertIn('/kops-20261027/manifest.json', entry)
        manifest = json.loads((public / 'kops-20261027/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['name'], 'KOps · 云枢')
        self.assertEqual(manifest['start_url'], '../')
        self.assertNotIn('spug_web/public/favicon.ico', UI)
        self.assertNotIn('spug_web/public/manifest.json', UI)


if __name__ == '__main__':
    unittest.main()
