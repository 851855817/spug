"""Personal dismissals change UI only; preserve the full backend and recovery contract."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).parent
PROFILE = 'personal-reminders-ui'
UI = {'spug_web/src/components/personalReminders.js', 'spug_web/src/components/ReminderDismiss.js',
      'spug_web/src/components/reminderDismiss.module.css', 'spug_web/src/pages/home/Todo.js',
      'spug_web/src/pages/home/index.module.less', 'spug_web/src/layout/Notification.js',
      'spug_web/src/pages/server-expiry/Reminders.js'}


def load(name):
    spec = importlib.util.spec_from_file_location('reminder_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class PersonalRemindersReleaseTests(unittest.TestCase):
    def test_exact_runtime_scope_rejects_missing_extra_ui_and_backend_changes(self):
        package = load('package')
        package.validate_runtime_changes(sorted(UI) + ['docs/guide.md', 'spug_api/apps/setting/test_reminder_preferences.py'], PROFILE)
        for names in ([], sorted(UI)[1:], sorted(UI) + ['spug_web/src/layout/Header.js'],
                      sorted(UI) + ['spug_api/apps/setting/user.py']):
            with self.subTest(names=names), self.assertRaises(ValueError): package.validate_runtime_changes(names, PROFILE)

    def test_backend_preservation_and_ui_failure_recovery_are_complete(self):
        fixtures = load('test_workbench_release'); fixtures.PROFILE = PROFILE
        case = fixtures.WorkbenchReleaseTests()
        case.test_manifest_and_image_preserve_both_api_modules_with_no_overlay()
        case.test_loading_stage_requires_complete_image_api_preservation_receipt()
        case.test_success_and_partial_failures_never_apply_api_and_use_new_paired_backups()
