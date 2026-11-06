import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent


class HostContractTests(unittest.TestCase):
    def module(self):
        path = HERE / 'host.py'
        self.assertTrue(path.is_file(), 'Missing exact resource release host adapter')
        spec = importlib.util.spec_from_file_location('resource_release_host', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_compose_uses_observed_image_tag_and_preserves_other_bytes(self):
        module = self.module()
        text = 'services:\n  spug:\n    image: spug-baota:d48d73b # existing\n    ports: [8080:80]\n'
        actual = module.next_compose(text, 'a' * 40)
        self.assertEqual(actual, text.replace('spug-baota:d48d73b', 'spug-baota:aaaaaaa'))
        with self.assertRaises(Exception): module.next_compose(text.replace('d48d73b', '4739172'), 'a' * 40)

    def test_startup_plan_is_four_frozen_migrations_and_no_production_generation(self):
        module = self.module()
        host = module.ResourceHost('a' * 40, 1)
        calls = []
        host.run = lambda command, **kw: calls.append((command, kw.get('input', ''))) or ''
        host.migrate()
        self.assertEqual(len(calls), 3)
        self.assertIn('0002_account_resources', calls[0][1])
        self.assertEqual(calls[1][0][-3:], ['migrate', '--noinput', '--verbosity=0'])
        self.assertNotIn('makemigrations', str(calls))

    def test_missing_prepared_receipts_fails_before_production_commands(self):
        module = self.module()
        host = module.ResourceHost('a' * 40, 1, root=HERE / 'not-a-live-stage')
        calls = []
        host.run = lambda *args, **kwargs: calls.append(args)
        with self.assertRaises(Exception): host.preflight()
        self.assertEqual(calls, [])

    def test_compatibility_restore_changes_compose_to_closed_image_and_never_starts(self):
        module = self.module()
        host = module.ResourceHost('a' * 40, 1)
        host.api_attempted = host.ui_attempted = False
        host.release = {'rollback_image': 'spug-baota:resource-rollback-aaaaaaa'}
        saved = []
        host.write_compose = saved.append
        host.validate_compose = lambda: None
        host.patch_entrypoint = lambda: None
        host.restore({'compose': 'image: spug-baota:d48d73b\n'}, True)
        self.assertEqual(saved, ['image: spug-baota:resource-rollback-aaaaaaa\n'])

    def test_maintenance_closes_all_eight_and_persists_gate_before_stopping(self):
        module = self.module()
        host = module.ResourceHost('a' * 40, 1)
        events = []
        host.stop_health = lambda: events.append('health')
        host.configure_runtime = lambda autostart, **kwargs: events.append(('gate', autostart))
        host.hold_entrypoint = lambda: events.append('hold-migrations')
        host.control = lambda action, *names, **kwargs: events.append((action, names)) or '0'
        host.services = lambda: dict({name: 'STOPPED' for name in module.SERVICES}, redis='RUNNING')
        host.close()
        self.assertEqual(events[1], 'hold-migrations')
        self.assertEqual(events[2], ('gate', False))
        self.assertEqual(events[3], ('stop', module.SERVICES))
        self.assertIn('spug-worker', module.SERVICES)
        self.assertIn('spug-monitor', module.SERVICES)
        self.assertNotIn('redis', module.SERVICES)

    def test_partial_ddl_restart_gate_never_runs_update_or_migration(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'entrypoint.sh'
            path.write_text('#!/bin/sh\npython3 manage.py updatedb\nexec supervisord\n')
            host = module.ResourceHost('a' * 40, 1)
            def execute(command, **kwargs):
                script = kwargs['input'].replace('/entrypoint.sh', path.as_posix())
                return subprocess.check_output([sys.executable, '-'], input=script, text=True).strip()
            host.run = execute
            host.hold_entrypoint()
            host.hold_entrypoint()
            self.assertIn(': account-resource-maintenance-migrations-held', path.read_text())
            self.assertNotIn('updatedb', path.read_text())
            self.assertNotIn('python3 manage.py migrate', path.read_text())
            host.patch_entrypoint()
            self.assertEqual(path.read_text().splitlines().count('python3 manage.py migrate --noinput'), 1)
            host.hold_entrypoint()
            self.assertNotIn('python3 manage.py migrate', path.read_text())

    def test_audit_rotation_follows_only_the_actual_persistent_data_bind(self):
        module = self.module()
        template = (HERE / 'resource-audit.logrotate').read_text()
        mounts = [{'Destination': '/data', 'Type': 'bind', 'Source': '/srv/spug-data', 'RW': True}]
        self.assertEqual(module.audit_rotation(mounts, template), template.replace('/data/logs/', '/srv/spug-data/logs/'))
        for changed in ([], mounts * 2, [dict(mounts[0], Type='volume')],
                        [dict(mounts[0], RW=False)], [dict(mounts[0], Source='/srv/../etc')]):
            with self.subTest(mounts=changed), self.assertRaises(Exception): module.audit_rotation(changed, template)

    def test_rotation_install_is_backed_up_exact_and_debug_checked_without_rotating(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = module.ResourceHost('a' * 40, 1)
            host.rotation_path = root / 'kops-resource-audit'
            host.rotation_text = '/srv/spug-data/logs/resource-access.log {\n daily\n rotate 14\n}\n'
            host.rotation_original = {'present': False}
            host.backup_directory = root / 'backup'
            host.backup_directory.mkdir()
            host.rotation_backed_up = True
            calls = []
            host.run = lambda command, **kwargs: calls.append(command) or ''
            original = host.rotation_state
            def state():
                result = original()
                if result['present']: result.update(mode=0o644, uid=0, gid=0)
                return result
            with mock.patch.object(module.os, 'chown', create=True), mock.patch.object(host, 'rotation_state', state):
                host.install_rotation()
                self.assertEqual(host.rotation_path.read_text(), host.rotation_text)
                self.assertIn(['logrotate', '--debug', '--state', '/dev/null', str(host.rotation_path)], calls)
                host.verify_rotation()
                host.rotation_path.write_text('unexpected profile')
                with self.assertRaises(Exception): host.verify_rotation()

    def test_external_scope_digest_rejects_rehashed_stage_before_commands(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            host = module.ResourceHost('a' * 40, 1, root=Path(directory), scope_sha256='b' * 64)
            host.stage.mkdir()
            (host.stage / 'scope.json').write_text('{}')
            calls = []
            host.run = lambda *args, **kwargs: calls.append(args)
            with self.assertRaisesRegex(Exception, 'resource-external-scope-hash-mismatch'): host.load()
            self.assertEqual(calls, [])

    def test_preflight_checks_observed_migration_bytes_in_each_live_root(self):
        module = self.module()
        expected = {'apps/' + node.split('/')[0] + '/migrations/' + node.split('/')[1] + '.py': proof['sha256']
                    for node, proof in module.contract.BASELINE['migration_graph'].items()}
        module.verify_live_history({'api': [dict(expected), dict(expected)]})
        changed = [dict(expected), dict(expected)]
        changed[1][next(iter(expected))] = 'f' * 64
        with self.assertRaises(Exception): module.verify_live_history({'api': changed})

    def test_runtime_config_rewrites_only_eight_programs_and_injects_persistent_audit(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'supervisord.conf'
            path.write_text(''.join('[program:%s]\nautostart=true\nautorestart=true\nenvironment=LANG="C"\n' % name
                                    for name in (*module.SERVICES, 'redis')))
            host = module.ResourceHost('a' * 40, 1)
            def execute(command, **kwargs):
                script = kwargs['input'].replace('/etc/supervisord.conf', path.as_posix())
                return subprocess.check_output([sys.executable, '-'], input=script, text=True).strip()
            host.run = execute
            host.configure_runtime(False)
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config.read(path)
            for name in module.SERVICES:
                self.assertFalse(config.getboolean('program:' + name, 'autostart'))
                self.assertIn('KOPS_RESOURCE_AUDIT_LOG="/data/logs/resource-access.log"', config['program:' + name]['environment'])
            self.assertTrue(config.getboolean('program:redis', 'autostart'))
            self.assertEqual(config['program:redis']['environment'], 'LANG="C"')


if __name__ == '__main__': unittest.main()
