"""Job drafts and inventory reminder controls must stay UI-only and reversible."""
from test_personal_reminders_release import load
import unittest

PROFILE = 'inventory-role-presets-ui'
UI = {'spug_web/src/pages/panel-inventory/index.js', 'spug_web/src/pages/system/role/PagePerm.js',
      'spug_web/src/pages/system/role/presets.js'}


class InventoryRolesReleaseTests(unittest.TestCase):
    def test_exact_scope_rejects_backend_permissions_and_additional_ui_changes(self):
        package=load('package')
        package.validate_runtime_changes(sorted(UI)+['spug_web/src/pages/system/role/presets.test.js'],PROFILE)
        for names in ([],sorted(UI)[1:],sorted(UI)+['spug_api/apps/account/views.py'],
                      sorted(UI)+['spug_web/src/pages/system/role/codes.js']):
            with self.subTest(names=names),self.assertRaises(ValueError):
                package.validate_runtime_changes(names,PROFILE)

    def test_full_api_preservation_and_ui_only_recovery_use_new_paired_backups(self):
        fixtures=load('test_workbench_release');fixtures.PROFILE=PROFILE
        case=fixtures.WorkbenchReleaseTests()
        case.test_manifest_and_image_preserve_both_api_modules_with_no_overlay()
        case.test_loading_stage_requires_complete_image_api_preservation_receipt()
        case.test_success_and_partial_failures_never_apply_api_and_use_new_paired_backups()
