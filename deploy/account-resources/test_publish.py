import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent


class HostFixture:
    def __init__(self, failure=None):
        self.failure, self.events, self.closed = failure, [], False

    def action(self, name):
        self.events.append(name)
        if name == self.failure: raise RuntimeError('sensitive details must never escape')
        return {'state': name}

    def preflight(self): return self.action('preflight')
    def backup_configuration(self, baseline): self.action('backup-configuration')
    def close(self): self.closed = True; self.action('close')
    def verify_baseline(self, baseline): self.action('verify-baseline')
    def backup_pair(self): self.action('backup-pair')
    def install_rotation(self): self.action('rotation')
    def overlay_api(self): self.action('api')
    def migrate(self): self.action('migrate')
    def verify_additions(self, baseline): return self.action('verify-additions')
    def initialize(self, baseline, upgraded): return self.action('initialize')
    def overlay_ui(self): self.action('ui')
    def verify_candidate(self, baseline, upgraded): self.action('verify-candidate')
    def compose_candidate(self): self.action('compose')
    def start_candidate(self): self.action('start')
    def open(self): self.action('open'); self.closed = False
    def verify_open(self, baseline, upgraded): self.action('verify-open')
    def verify_window(self, baseline, upgraded): self.action('verify-window')
    def restore(self, baseline, compatibility): self.action('restore-compat' if compatibility else 'restore')
    def verify_restored(self, baseline, upgraded, compatibility): self.action('verify-restored')
    def record(self, report): self.action('record')
    def resume_health(self): self.action('resume-health')


class PublicationFailureTests(unittest.TestCase):
    def module(self):
        path = HERE / 'publish.py'
        self.assertTrue(path.is_file(), 'Missing resource-specific closed rollback controller')
        spec = importlib.util.spec_from_file_location('resource_publisher', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_success_requires_explicit_initialization_before_ui_and_open(self):
        host = HostFixture()
        result = self.module().publish(host)
        self.assertTrue(result['published'])
        self.assertLess(host.events.index('initialize'), host.events.index('ui'))
        self.assertLess(host.events.index('backup-pair'), host.events.index('api'))
        self.assertLess(host.events.index('backup-pair'), host.events.index('rotation'))
        self.assertLess(host.events.index('rotation'), host.events.index('api'))
        self.assertFalse(host.closed)

    def test_every_post_maintenance_failure_keeps_old_code_closed(self):
        publisher = self.module()
        for name in ('backup-pair', 'rotation', 'api', 'migrate', 'initialize', 'ui', 'compose', 'start', 'verify-open', 'resume-health'):
            with self.subTest(name=name):
                host = HostFixture(name)
                result = publisher.publish(host)
                self.assertFalse(result['published'])
                self.assertTrue(result['maintenance_closed'])
                self.assertTrue(host.closed)
                self.assertNotIn('sensitive', str(result))
                if 'restore-compat' in host.events:
                    self.assertNotIn('open', host.events[host.events.index('restore-compat') + 1:])

    def test_partial_ddl_or_new_work_after_open_never_attempts_rollback(self):
        publisher = self.module()
        for name in ('verify-additions', 'verify-window'):
            host = HostFixture(name)
            if name == 'verify-window':
                host.verify_open = lambda *args: (_ for _ in ()).throw(RuntimeError('failed-open'))
            result = publisher.publish(host)
            self.assertFalse(result['rolled_back'])
            self.assertTrue(result['maintenance_closed'])
            self.assertNotIn('restore-compat', host.events)

    def test_preflight_failure_does_not_close_or_backup_healthy_service(self):
        host = HostFixture('preflight')
        result = self.module().publish(host)
        self.assertEqual(host.events, ['preflight'])
        self.assertFalse(result['maintenance_closed'])


if __name__ == '__main__': unittest.main()
