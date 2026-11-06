"""Current SSL results use the existing strict one-service publication contract."""
import importlib.util
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('ssl_current_single_service_contract',
    HERE / 'test_ssl_reminder_actions_release.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class SSLCurrentResultsReleaseTests(contract.SSLReminderActionsReleaseTests):
    def setUp(self):
        for name, value in (('PROFILE', 'ssl-current-results'),
                            ('UI', {'spug_web/src/pages/ssl-monitor/index.js'})):
            patcher = mock.patch.object(contract, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_original_reminder_profile_still_rejects_missing_reminder_ui(self):
        package = contract.load('package')
        names = ['spug_api/' + contract.MODULE, 'spug_web/src/pages/ssl-monitor/index.js']
        with self.assertRaises(ValueError):
            package.validate_runtime_changes(names, 'ssl-reminder-actions')
        with self.assertRaises(ValueError):
            package.validate_runtime_changes(names + ['spug_web/src/pages/ssl-monitor/Reminders.js'],
                'ssl-current-results')
