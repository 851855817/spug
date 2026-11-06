"""Exercise real publication failure handling without Docker or production access."""
import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


PUBLISHER = Path(__file__).with_name('publish-expiry.py')


class Response(io.BytesIO):
    status = 200


class ReleaseTests(unittest.TestCase):
    def test_staged_overlay_helper_matches_publication_and_restore_commands(self):
        tree = ast.parse(PUBLISHER.read_text(encoding='utf-8'))
        staged = next(node.iter for node in tree.body if isinstance(node, ast.For) and
                      isinstance(node.target, ast.Name) and node.target.id == 'name')
        names = {item.value for item in staged.elts}
        overlays = [node for node in ast.walk(tree) if isinstance(node, ast.BinOp) and
                    isinstance(node.left, ast.Name) and node.left.id == 'dest' and
                    isinstance(node.right, ast.Constant) and node.right.value.startswith('/apply-')]
        self.assertEqual(len(overlays), 2)
        for command in overlays:
            self.assertIn(command.right.value.lstrip('/'), names, 'Overlay command refers to an unstaged helper')

    def failure(self, failure):
        self.assertTrue(PUBLISHER.exists(), 'expiry publisher missing')
        tree = ast.parse(PUBLISHER.read_text(encoding='utf-8'), filename=str(PUBLISHER))
        release = next(node for node in tree.body if isinstance(node, ast.Try))
        release.body = ast.parse("raise RuntimeError('injected failure')").body
        module = ast.fix_missing_locations(ast.Module(body=[release], type_ignores=[]))
        events, saved = [], {}
        with TemporaryDirectory() as directory:
            root = Path(directory); backup = root / 'backup'; backup.mkdir()
            (root / 'platform-health').mkdir()
            (backup / 'spug-health.py').write_text('previous health script')
            compose = root / 'docker-compose.yml'; compose.write_text('image: spug-baota:8a64ca1\n')

            def run(*args, **kwargs):
                events.append(('run', args))
                if args[-1] == 'restore' and 'apply-expiry.py' in args[-3] and failure == 'api-restore':
                    raise RuntimeError('restore failed')
                if args[-1] == 'restore' and 'deploy-compact-ui.py' in args[-3] and failure == 'ui-restore':
                    raise RuntimeError('ui restore failed')
                if ('cp' in args and str(args[-1]).endswith('/apps/ops/models.py') and failure == 'compat-expiry'):
                    raise RuntimeError('compatibility failed')
                if '-c' in args:
                    if 'connection.introspection' in args[-1]:
                        if failure == 'schema-unknown':
                            raise RuntimeError('schema unknown')
                        return '1'
                    if 'spug-ui-backups' in args[-1] and failure == 'ui-restore':
                        raise RuntimeError('ui restore failed')
                if 'inspect' in args:
                    return 'container-id 0'
                return ''

            def control(*args):
                events.append(('control', args))
                if args[0] == 'status':
                    return '\n'.join('service-%s RUNNING' % i for i in range(9))
                if args[0] == 'pid':
                    return 'pid-old'
                return ''

            def process(args, **kwargs):
                events.append(('process', tuple(args)))
                return SimpleNamespace(returncode=0)

            def urlopen(url, **kwargs):
                events.append(('urlopen', (url,)))
                return Response(json.dumps({'revision': 'previous-ui'}).encode())

            environment = dict(root=root, backup=backup, compose=compose, previous_compose=compose.read_text(),
                revision='a'*40, dest='/tmp/expiry-fixture', overlay=failure != 'api-publication-unverified',
                overlay_attempted=True, ui_attempted=failure in ('ui-restore', 'ui-publication-unverified'),
                ui_published=failure == 'ui-restore',
                migration_started=True, safe_to_resume_health=False, maintenance=('nginx', 'spug-api'),
                workers=('spug-worker', 'spug-monitor'), worker_pids={'spug-worker': 'pid-old', 'spug-monitor': 'pid-old'},
                container='container-id 0', before={'digest': 'unchanged'}, previous_ui_revision='previous-ui',
                fingerprint=lambda: {'digest': 'unchanged'}, run=run, control=control,
                save=lambda name, data: saved.update({name: data}),
                subprocess=SimpleNamespace(run=process, DEVNULL=-3),
                urllib=SimpleNamespace(request=SimpleNamespace(urlopen=urlopen)), shutil=shutil, json=json)
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'injected failure'):
                exec(compile(module, str(PUBLISHER), 'exec'), environment)
            expected = 'schema-verification-required-before-service-start' if failure == 'schema-unknown' else failure
            self.assertIn(expected, saved['rollback.json']['errors'])
            self.assertFalse(environment['safe_to_resume_health'])
            starts = [(kind, args) for kind, args in events if (kind == 'control' and args[0] == 'start') or
                (kind in ('run', 'process') and any(args[i:i+2] == pair for i in range(len(args)-1)
                    for pair in (('supervisorctl', 'start'), ('systemctl', 'start'))))]
            self.assertEqual(starts, [], 'Failed rollback must not reopen ingress or health recovery')

    def test_api_restore_failure_stays_closed(self):
        self.failure('api-restore')

    def test_schema_unknown_stays_closed(self):
        self.failure('schema-unknown')

    def test_expiry_model_restore_failure_stays_closed(self):
        self.failure('compat-expiry')

    def test_ui_restore_failure_stays_closed(self):
        self.failure('ui-restore')

    def test_api_helper_failure_before_success_receipt_stays_closed(self):
        self.failure('api-publication-unverified')

    def test_ui_helper_failure_before_success_receipt_stays_closed(self):
        self.failure('ui-publication-unverified')


if __name__ == '__main__':
    unittest.main()
