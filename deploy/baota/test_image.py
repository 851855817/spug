"""Offline checks against a built image, without starting services or mounting data."""
import configparser
from pathlib import Path
import sys
import unittest


ORIGINAL_PROGRAMS = {'nginx', 'redis', 'spug-api', 'spug-ws', 'spug-worker',
                     'spug-monitor', 'spug-scheduler'}


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser(interpolation=None)
        self.assertEqual(self.config.read('/etc/supervisord.conf'), ['/etc/supervisord.conf'])
        self.entrypoint = Path('/entrypoint.sh').read_text()

    def programs(self):
        return {section[len('program:'):] for section in self.config.sections()
                if section.startswith('program:')}

    def test_inventory_model_package_is_present(self):
        root = Path('/opt/spug/spug_api/apps/baota')
        self.assertIn('inventory.models import', (root / 'models.py').read_text())
        for name in ('__init__.py', 'models.py', 'readers.py', 'worker.py', 'service.py'):
            self.assertTrue((root / 'inventory' / name).is_file(), 'Missing inventory package: ' + name)
        # The pre-task rollback predates resource monitoring too. Current
        # baota.models imports both packages during Django model discovery.
        for name in ('__init__.py', 'models.py'):
            self.assertTrue((root / 'resource_monitor' / name).is_file(), 'Missing monitor model: ' + name)

    def test_persistent_key_wrapper_is_preserved(self):
        wrapper = Path('/baota-entrypoint.sh').read_text()
        self.assertIn("path = Path(os.environ.get('BAOTA_KEY_FILE', '/data/baota.key'))", wrapper)
        self.assertIn('Encryption key fingerprint changed', wrapper)
        self.assertIn('exec /entrypoint.sh "$@"', wrapper)


class WorkerImageTests(ImageTests):
    def test_worker_is_loaded_from_the_real_supervisor_config(self):
        self.assertEqual(self.programs(), ORIGINAL_PROGRAMS | {'spug-panel-worker', 'spug-ops-worker'})
        worker = self.config['program:spug-panel-worker']
        self.assertEqual(worker['directory'], '/data/spug/spug_api')
        self.assertEqual(worker['command'], 'python3 manage.py runpanelworker --parallel 2')
        self.assertEqual(worker['stopsignal'], 'TERM')
        self.assertGreaterEqual(int(worker['stopwaitsecs']), 35)
        for name in ('autostart', 'autorestart', 'stopasgroup', 'killasgroup', 'redirect_stderr'):
            self.assertTrue(worker.getboolean(name))
        self.assertEqual(worker['stdout_logfile'], '/data/spug/spug_api/logs/panel-worker.log')
        self.assertNotIn('environment', worker)
        self.assertTrue(Path('/opt/spug/spug_api/apps/baota/management/commands/runpanelworker.py').is_file())

    def test_ops_worker_is_separate_and_bounded(self):
        worker = self.config['program:spug-ops-worker']
        self.assertEqual(worker['directory'], '/data/spug/spug_api')
        self.assertEqual(worker['command'], 'python3 manage.py runopsworker')
        self.assertGreaterEqual(int(worker['stopwaitsecs']), 75)
        self.assertTrue(Path('/opt/spug/spug_api/apps/ops/management/commands/runopsworker.py').is_file())
        self.assertNotIn('environment', worker)
        self.assertTrue(worker.getboolean('stopasgroup'))

    def test_upstream_sync_and_migration_still_precede_supervisor(self):
        self.assertEqual(self.entrypoint.splitlines().count('python3 manage.py updatedb'), 1)
        self.assertLess(self.entrypoint.index('rsync '), self.entrypoint.index('python3 manage.py updatedb'))
        self.assertLess(self.entrypoint.index('python3 manage.py updatedb'),
                        self.entrypoint.index('exec supervisord -c /etc/supervisord.conf'))


class RollbackImageTests(ImageTests):
    def test_compatibility_overlay_matches_reviewed_sources(self):
        for filename in ('models.py', 'views.py', 'validation.py'):
            actual = Path('/opt/spug/spug_api/apps/baota', filename).read_bytes()
            expected = Path('/compat-source', filename).read_bytes()
            self.assertTrue(actual == expected, 'Rollback compatibility file differs: ' + filename)

    def test_rollback_keeps_old_services_and_never_generates_migrations(self):
        self.assertEqual(self.programs(), ORIGINAL_PROGRAMS)
        self.assertNotIn('updatedb', self.entrypoint)
        self.assertNotIn('makemigrations', self.entrypoint)
        self.assertEqual(self.entrypoint.splitlines().count('python3 manage.py migrate --noinput'), 1)
        self.assertLess(self.entrypoint.index('rsync '), self.entrypoint.index('python3 manage.py migrate --noinput'))
        self.assertLess(self.entrypoint.index('python3 manage.py migrate --noinput'),
                        self.entrypoint.index('exec supervisord -c /etc/supervisord.conf'))


class ExpiryImageTests(WorkerImageTests):
    def test_expiry_models_and_daily_scheduler_are_in_the_startup_source(self):
        root = Path('/opt/spug/spug_api/apps')
        self.assertIn('expiry.models import', (root / 'ops/models.py').read_text())
        for name in ('__init__.py', 'models.py', 'dates.py', 'assets.py', 'service.py', 'reminders.py', 'urls.py'):
            self.assertTrue((root / 'ops/expiry' / name).is_file(), 'Missing expiry module: ' + name)
        scheduler = (root / 'schedule/scheduler.py').read_text()
        self.assertIn('server-expiry-daily', scheduler)
        self.assertIn('scheduled_check_expiry', scheduler)


class UploadRollbackImageTests(ImageTests):
    def test_no_reverse_schema_generation_with_existing_worker_wiring(self):
        self.assertEqual(self.programs(), ORIGINAL_PROGRAMS | {'spug-panel-worker', 'spug-ops-worker'})
        self.assertFalse(self.config['program:spug-panel-worker'].getboolean('autostart'))
        self.assertFalse(self.config['program:spug-panel-worker'].getboolean('autorestart'))
        self.assertFalse(self.config['program:spug-ops-worker'].getboolean('autostart'))
        self.assertFalse(self.config['program:spug-ops-worker'].getboolean('autorestart'))
        self.assertNotIn('updatedb', self.entrypoint)
        self.assertNotIn('makemigrations', self.entrypoint)
        self.assertEqual(self.entrypoint.splitlines().count('python3 manage.py migrate --noinput'), 1)
        actual = Path('/opt/spug/spug_api/apps/baota/models.py').read_bytes()
        self.assertEqual(actual, Path('/compat-source/models.py').read_bytes())
        self.assertIn(b'class PanelUploadResult', actual)


if __name__ == '__main__':
    cases = {'worker': WorkerImageTests, 'expiry': ExpiryImageTests, 'rollback': RollbackImageTests, 'upload-rollback': UploadRollbackImageTests}
    case = cases[sys.argv.pop(1)]
    unittest.main(defaultTest=case.__name__)
