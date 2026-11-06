import importlib.util
import copy
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
        text = 'services:\n  spug:\n    image: spug-baota:4739172 # existing\n    ports: [8080:80]\n'
        actual = module.next_compose(text, 'a' * 40)
        self.assertEqual(actual, text.replace('spug-baota:4739172', 'spug-baota:aaaaaaa'))
        with self.assertRaises(Exception): module.next_compose(text.replace('4739172', 'd48d73b'), 'a' * 40)
        with self.assertRaises(Exception): module.next_compose(text + 'image: spug-baota:4739172\n', 'a' * 40)
        self.assertEqual(module.contract.BASELINE['image_tag'], 'spug-baota:d48d73b')

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
        host.restore({'compose': 'image: spug-baota:4739172\n'}, True)
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

    def volume_facts(self):
        source = '/var/lib/docker/volumes/spug_spug-data/_data'
        mount = {'Destination': '/data', 'Type': 'volume', 'Name': 'spug_spug-data', 'Source': source, 'RW': True}
        volume = {'Name': 'spug_spug-data', 'Driver': 'local', 'Mountpoint': source, 'Scope': 'local', 'Options': None}
        return mount, volume

    def test_audit_rotation_follows_only_the_exact_persistent_local_named_volume(self):
        module = self.module()
        template = (HERE / 'resource-audit.logrotate').read_text()
        mount, volume = self.volume_facts()
        mounts = [mount]
        self.assertEqual(module.audit_rotation(mounts, template, volume),
                         template.replace('/data/logs/', mount['Source'] + '/logs/'))
        for changed in ([], mounts * 2, [dict(mount, Type='bind')], [dict(mount, Name='other')],
                        [dict(mount, RW=False)], [dict(mount, Source='/srv/../etc')],
                        mounts + [dict(mount, Destination='/data/logs')]):
            with self.subTest(mounts=changed), self.assertRaises(Exception): module.audit_rotation(changed, template, volume)

    def test_rotation_requires_volume_inspection_identity_driver_scope_and_no_options(self):
        module = self.module()
        mount, volume = self.volume_facts()
        template = (HERE / 'resource-audit.logrotate').read_text()
        for key, value in (('Name', 'other'), ('Driver', 'nfs'), ('Scope', 'global'),
                           ('Mountpoint', '/var/lib/other'), ('Options', {'type': 'nfs'}), ('Options', {})):
            with self.subTest(key=key, value=value), self.assertRaises(Exception):
                module.audit_rotation([mount], template, dict(volume, **{key: value}))

    def test_storage_path_rejects_symlink_parents_before_rotation(self):
        module = self.module()
        path = mock.Mock()
        path.is_dir.return_value = True
        path.is_symlink.return_value = False
        path.parents = [mock.Mock()]
        path.parents[0].is_symlink.return_value = True
        with self.assertRaises(Exception): module.verify_storage_path(path)
        path.parents[0].is_symlink.return_value = False
        module.verify_storage_path(path)

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
        opt = {'apps/monitor/migrations/0002_ssl_monitor.py': expected['apps/monitor/migrations/0002_ssl_monitor.py']}
        roots = [opt, dict(expected)]
        module.verify_live_history({'api': roots})
        for mode in ('changed-opt', 'added-opt', 'missing-data', 'changed-data', 'swapped-roots'):
            changed = copy.deepcopy(roots)
            if mode == 'changed-opt': changed[0][next(iter(opt))] = 'f' * 64
            if mode == 'added-opt': changed[0]['apps/account/migrations/0001_initial.py'] = expected['apps/account/migrations/0001_initial.py']
            if mode == 'missing-data': changed[1].pop(next(iter(expected)))
            if mode == 'changed-data': changed[1][next(iter(expected))] = 'f' * 64
            if mode == 'swapped-roots': changed.reverse()
            with self.subTest(mode=mode), self.assertRaises(Exception): module.verify_live_history({'api': changed})

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
