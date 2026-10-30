"""Execute the publisher's real rollback branch without Docker or network access."""
import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


PUBLISHER = Path(__file__).with_name('publish-inventory.py')


class FakeResponse(io.BytesIO):
    status = 200


class RollbackSafetyTests(unittest.TestCase):
    def run_failure(self, failure):
        # Keep the production except/finally intact. Only replace publication
        # with a failure so these tests never execute the script's setup phase.
        tree = ast.parse(PUBLISHER.read_text(), filename=str(PUBLISHER))
        release = next(node for node in tree.body if isinstance(node, ast.Try))
        release.body = ast.parse("raise RuntimeError('injected publication failure')").body
        module = ast.fix_missing_locations(ast.Module(body=[release], type_ignores=[]))
        events, saved = [], {}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'backup'
            backup.mkdir()
            (backup / 'spug-health.py').write_text('previous health script')
            (root / 'platform-health').mkdir()
            compose = root / 'docker-compose.yml'
            previous_compose = 'image: spug-baota:2a2eb36\n'
            compose.write_text(previous_compose)

            def run(*args, **kwargs):
                events.append(('run', args))
                if args[-1] == 'restore' and failure == 'api-restore':
                    raise RuntimeError('injected API restore failure')
                if ('cp' in args and str(args[-1]).endswith('/apps/baota/models.py')
                        and failure == 'compat-model'):
                    raise RuntimeError('injected compatibility restore failure')
                if '-c' in args:
                    if 'connection.introspection' in args[-1]:
                        if failure == 'schema-unknown':
                            raise RuntimeError('injected schema introspection failure')
                        return '1'
                    if 'spug-ui-backups' in args[-1] and failure == 'ui-restore':
                        raise RuntimeError('injected UI restore failure')
                if 'inspect' in args:
                    return 'container-id 0'
                return ''

            def control(*args):
                events.append(('control', args))
                if args[0] == 'pid':
                    return 'pid-old'
                if args[0] == 'status':
                    return '\n'.join('service-%s RUNNING' % index for index in range(9))
                return ''

            def process_run(args, **kwargs):
                events.append(('process', tuple(args)))
                return SimpleNamespace(returncode=0)

            def urlopen(url, **kwargs):
                events.append(('urlopen', (url,)))
                return FakeResponse(json.dumps({'revision': 'previous-ui'}).encode())

            environment = {
                'root': root, 'backup': backup, 'compose': compose, 'previous_compose': previous_compose,
                'revision': 'a' * 40, 'dest': '/tmp/private-inventory-fixture',
                'overlay': True, 'ui_published': failure == 'ui-restore', 'migration_started': True,
                'safe_to_resume_health': False, 'maintenance': ('nginx', 'spug-api'),
                'workers': ('spug-worker', 'spug-monitor'),
                'worker_pids': {'spug-worker': 'pid-old', 'spug-monitor': 'pid-old'},
                'container': 'container-id 0', 'before': {'business-digest': 'unchanged'},
                'previous_ui_revision': 'previous-ui', 'fingerprint': lambda: {'business-digest': 'unchanged'},
                'run': run, 'control': control, 'save': lambda name, data: saved.update({name: data}),
                'subprocess': SimpleNamespace(run=process_run, DEVNULL=-3),
                'urllib': SimpleNamespace(request=SimpleNamespace(urlopen=urlopen)),
                'shutil': shutil, 'json': json,
            }
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'injected publication failure'):
                exec(compile(module, str(PUBLISHER), 'exec'), environment)

            expected = ('schema-verification-required-before-service-start'
                        if failure == 'schema-unknown' else failure)
            self.assertIn(expected, environment['rollback_errors'])
            self.assertEqual(saved['rollback.json']['errors'], environment['rollback_errors'])
            self.assertFalse(environment['safe_to_resume_health'])
            starts = [(kind, args) for kind, args in events if
                      (kind == 'control' and args[0] == 'start') or
                      (kind in ('run', 'process') and any(args[index:index + 2] == pair
                       for index in range(len(args) - 1)
                       for pair in (('supervisorctl', 'start'), ('systemctl', 'start'))))]
            self.assertEqual(starts, [], 'A failed rollback must keep ingress and health stopped')
            self.assertFalse(any(kind == 'urlopen' for kind, _ in events))
            if failure == 'schema-unknown':
                self.assertIsNone(saved['rollback.json']['schema_retained'])

    def test_api_restore_failure_keeps_ingress_and_health_stopped(self):
        self.run_failure('api-restore')

    def test_compatibility_model_restore_failure_keeps_ingress_and_health_stopped(self):
        self.run_failure('compat-model')

    def test_ui_restore_failure_keeps_ingress_and_health_stopped(self):
        self.run_failure('ui-restore')

    def test_unknown_schema_keeps_ingress_and_health_stopped(self):
        self.run_failure('schema-unknown')


if __name__ == '__main__':
    unittest.main()
