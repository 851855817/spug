"""Exercise failures at the schema boundary without Docker or a live DB."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('ssl_publisher', Path(__file__).with_name('publish.py'))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class FakeHost:
    def __init__(self, fail='', complete=True):
        self.fail = fail
        self.complete = complete
        self.events = []
        self.closed = False
        self.phase = 'old'

    def event(self, name):
        self.events.append(name)
        if self.fail == name:
            raise RuntimeError('SECRET raw error')

    def preflight(self):
        self.event('preflight')
        return {'original': True}

    def backup_configuration(self, baseline): self.event('configuration')
    def close(self):
        self.closed = True
        self.event('close')
    def verify_baseline(self, baseline): self.event('baseline')
    def backup_pair(self): self.event('pair')
    def overlay_api(self): self.event('api')
    def migrate(self):
        self.phase = 'migrating'
        self.event('migration')
        self.phase = 'new'
    def verify_additions(self, baseline):
        self.event('additions')
        if not self.complete:
            raise RuntimeError('partial schema')
        return {'schema': 'verified'}
    def overlay_ui(self): self.event('ui')
    def verify_candidate(self, baseline, upgraded): self.event('candidate')
    def compose_candidate(self): self.event('compose')
    def start_candidate(self): self.event('start')
    def open(self):
        self.event('open')
        self.closed = False
    def verify_open(self, baseline, upgraded): self.event('http')
    def verify_window(self, baseline, upgraded): self.event('window')
    def resume_health(self): self.event('health')
    def restore(self, baseline, compatibility):
        self.events.append('restore-compat' if compatibility else 'restore-original')
        self.event('restore')
    def verify_restored(self, baseline, upgraded, compatibility): self.event('restored')
    def start_restored(self, compatibility): self.event('restart')
    def verify_old_http(self): self.event('old-http')
    def record(self, report): self.events.append('record')


class PublicationTests(unittest.TestCase):
    def test_success_requires_backup_before_ddl_and_verify_before_open(self):
        host = FakeHost()
        report = publisher.publish(host)
        self.assertTrue(report['published'])
        self.assertLess(host.events.index('pair'), host.events.index('api'))
        self.assertLess(host.events.index('migration'), host.events.index('ui'))
        self.assertLess(host.events.index('candidate'), host.events.index('open'))

    def test_preflight_failure_leaves_running_service_alone(self):
        host = FakeHost('preflight')
        report = publisher.publish(host)
        self.assertFalse(report['published'])
        self.assertFalse(host.closed)
        self.assertNotIn('close', host.events)

    def test_failed_backup_never_overlays_and_restores_old_processes(self):
        host = FakeHost('pair')
        report = publisher.publish(host)
        self.assertTrue(report['rolled_back'])
        self.assertNotIn('api', host.events)
        self.assertIn('restore-original', host.events)

    def test_complete_migration_failure_retains_compatible_models(self):
        host = FakeHost('migration')
        report = publisher.publish(host)
        self.assertTrue(report['rolled_back'])
        self.assertIn('restore-compat', host.events)

    def test_partial_migration_keeps_maintenance_closed(self):
        host = FakeHost('migration', complete=False)
        report = publisher.publish(host)
        self.assertFalse(report['rolled_back'])
        self.assertTrue(report['maintenance_closed'])
        self.assertNotIn('restart', host.events)
        self.assertNotIn('restore-compat', host.events)

    def test_post_ingress_business_drift_prevents_downgrade(self):
        host = FakeHost('http')
        host.verify_window = lambda *_args: (_ for _ in ()).throw(RuntimeError('business drift'))
        report = publisher.publish(host)
        self.assertTrue(report['maintenance_closed'])
        self.assertNotIn('restart', host.events)

    def test_post_ingress_http_failure_can_restore_in_closed_window(self):
        host = FakeHost('http')
        report = publisher.publish(host)
        self.assertTrue(report['rolled_back'])
        self.assertIn('window', host.events)

    def test_unverified_restore_never_restarts(self):
        host = FakeHost('candidate')
        original = host.verify_restored
        def fail(*args):
            original(*args)
            raise RuntimeError('SECRET')
        host.verify_restored = fail
        report = publisher.publish(host)
        self.assertTrue(report['maintenance_closed'])
        self.assertNotIn('restart', host.events)
        self.assertNotIn('SECRET', str(report))


if __name__ == '__main__':
    unittest.main()
