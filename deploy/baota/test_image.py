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

    def test_persistent_key_wrapper_is_preserved(self):
        wrapper = Path('/baota-entrypoint.sh').read_text()
        self.assertIn("path = Path(os.environ.get('BAOTA_KEY_FILE', '/data/baota.key'))", wrapper)
        self.assertIn('Encryption key fingerprint changed', wrapper)
        self.assertIn('exec /entrypoint.sh "$@"', wrapper)


class WorkerImageTests(ImageTests):
    def test_worker_is_loaded_from_the_real_supervisor_config(self):
        self.assertEqual(self.programs(), ORIGINAL_PROGRAMS | {'spug-panel-worker'})
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


if __name__ == '__main__':
    cases = {'worker': WorkerImageTests, 'rollback': RollbackImageTests}
    case = cases[sys.argv.pop(1)]
    unittest.main(defaultTest=case.__name__)
