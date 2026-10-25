"""Check release scope, receipts and archive boundaries without production access."""
import importlib.util
import ast
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).parent


class PackagingTests(unittest.TestCase):
    def helper(self, filename):
        path = ROOT / filename
        self.assertTrue(path.exists(), 'Upgrade review release helper is not implemented')
        spec = importlib.util.spec_from_file_location('upgrade_' + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_scope_refuses_model_settings_or_other_runtime_changes(self):
        package = self.helper('package.py')
        api = 'spug_api/apps/baota/operations/php_update.py'
        package.validate_runtime_changes([api, 'spug_api/apps/baota/test_php_update.py', 'docs/guide.md'])
        for name in ('spug_api/apps/ops/models.py', 'spug_api/spug/settings.py',
                     'spug_api/apps/schedule/scheduler.py', 'spug_api/apps/baota/operations/software.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.validate_runtime_changes([api, name])

    def test_receipt_contains_only_the_reviewed_api_file_and_all_ui_assets(self):
        package = self.helper('package.py')
        with TemporaryDirectory() as folder:
            stage = Path(folder)
            api = stage / 'source/spug_api/apps/baota/operations/php_update.py'
            api.parent.mkdir(parents=True); api.write_text('new runtime')
            build = stage / 'build'; build.mkdir()
            (build / 'index.html').write_text('new index')
            (build / 'asset-manifest.json').write_text('{}')
            for name in ('apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'):
                (stage / name).write_text('# checked helper ' + name)
            manifest = package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old runtime')
            self.assertEqual(manifest['previous_revision'], 'b' * 40)
            self.assertEqual(set(manifest['api_sha256']), {'apps/baota/operations/php_update.py'})
            self.assertNotEqual(manifest['api_sha256'], manifest['previous_api_sha256'])
            self.assertEqual(json.loads((stage / 'api-release.json').read_text())['sha256'], manifest['api_sha256'])
            ui = json.loads((build / 'ui-release.json').read_text())
            self.assertEqual(set(ui['sha256']), {'index.html', 'asset-manifest.json'})
            self.assertNotIn('ui-release.json', ui['sha256'])
            self.assertEqual(set(manifest['helper_sha256']), {'apply-expiry.py', 'deploy-compact-ui.py', 'fingerprint.py', 'release_safety.py'})

    def test_unsafe_archive_is_rejected_before_extracting_any_member(self):
        prepare = self.helper('prepare.py')
        for name in ('../outside', '/absolute', 'upgrade-aaaaaaa/../../outside',
                     'wrong-stage/file', 'upgrade-aaaaaaa/back\\slash'):
            with self.subTest(name=name), TemporaryDirectory() as folder:
                root = Path(folder)
                archive = root / 'input.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    data = b'test'
                    good = tarfile.TarInfo('upgrade-aaaaaaa/good'); good.size = len(data)
                    output.addfile(good, io.BytesIO(data))
                    bad = tarfile.TarInfo(name); bad.size = len(data)
                    output.addfile(bad, io.BytesIO(data))
                with self.assertRaises(ValueError):
                    prepare.safe_extract(archive, root, 'upgrade-aaaaaaa')
                self.assertFalse((root / 'upgrade-aaaaaaa').exists())

    def test_archive_links_are_rejected(self):
        prepare = self.helper('prepare.py')
        with TemporaryDirectory() as folder:
            root = Path(folder); archive = root / 'input.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                member = tarfile.TarInfo('upgrade-aaaaaaa/link')
                member.type = tarfile.SYMTYPE; member.linkname = '/etc'
                output.addfile(member)
            with self.assertRaises(ValueError):
                prepare.safe_extract(archive, root, 'upgrade-aaaaaaa')

    def test_built_image_checks_actual_runtime_and_each_static_asset(self):
        checker = self.helper('check_image.py')
        package = self.helper('package.py')
        with TemporaryDirectory() as folder:
            stage = Path(folder) / 'stage'; stage.mkdir()
            runtime = Path(folder) / 'runtime'; runtime.mkdir()
            api_name = 'apps/baota/operations/php_update.py'
            for base in (stage / 'source/spug_api', runtime / 'spug_api'):
                path = base / api_name; path.parent.mkdir(parents=True)
                path.write_text('new runtime')
            for base in (stage / 'build', runtime / 'spug_web/build'):
                base.mkdir(parents=True)
                (base / 'index.html').write_text('new index')
                (base / 'asset-manifest.json').write_text('{}')
                (base / 'new.js').write_text('new asset')
            for name in package.HELPERS:
                (stage / name).write_text('# helper')
            package.write_manifests(stage, 'a' * 40, 'b' * 40, b'old runtime')
            ui_receipt = (stage / 'build/ui-release.json').read_bytes()
            (runtime / 'spug_web/build/ui-release.json').write_bytes(ui_receipt)
            checker.verify(runtime, stage, 'a' * 40)
            for path in (runtime / 'spug_api' / api_name, runtime / 'spug_web/build/new.js',
                         runtime / 'spug_web/build/ui-release.json'):
                with self.subTest(path=path):
                    content = path.read_bytes(); path.write_bytes(b'wrong image content')
                    with self.assertRaises(ValueError):
                        checker.verify(runtime, stage, 'a' * 40)
                    path.write_bytes(content)

    def test_preservation_covers_all_business_models_and_only_excludes_monitor_cache(self):
        from types import SimpleNamespace
        def model(label, name):
            return type(name, (), {'_meta': SimpleNamespace(app_label=label)})
        candidates = [model(label, name) for label, name in (
            ('baota', 'Panel'), ('ops', 'ExpiryMetadata'), ('credential', 'Credential'),
            ('config', 'Config'), ('notify', 'Notify'), ('alarm', 'Alarm'), ('home', 'Notice'),
            ('app', 'Deploy'), ('account', 'User_roles'), ('baota', 'ResourceMonitorSnapshot'),
            ('baota', 'ResourceMonitorLease'), ('other', 'ResourceMonitorSnapshot'))]
        class Registry:
            def get_models(self, include_auto_created=False):
                self.included_relations = include_auto_created
                return candidates
        registry = Registry(); scope = {'apps': registry}
        tree = ast.parse((ROOT / 'fingerprint.py').read_text())
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id in ('labels', 'models')]
        exec(compile(ast.Module(body=assignments, type_ignores=[]), '<model-selection>', 'exec'), scope)
        self.assertTrue(registry.included_relations)
        self.assertEqual(scope['models'], candidates[:9] + candidates[-1:])

    def test_isolated_checks_bind_the_exact_image_and_full_revision(self):
        prepare = self.helper('prepare.py')
        image_id = 'sha256:' + 'c' * 64
        image = {'Id': image_id, 'Config': {'Labels': {'org.opencontainers.image.revision': 'a' * 40}}}
        self.assertEqual(prepare.image_identity(image, 'a' * 40), image_id)
        for wrong in ('sha256:short', 'spug-baota:aaaaaaa', ''):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                prepare.image_identity(dict(image, Id=wrong), 'a' * 40)
        with self.assertRaises(ValueError):
            prepare.image_identity(image, 'b' * 40)

    def test_schema_fingerprint_includes_cache_migrations_and_unmodeled_tables(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        descriptions = {'baota_monitor_snapshots': ['cached-column'],
                        'django_migrations': ['applied-column'], 'unmodeled_table': ['column']}
        introspection = SimpleNamespace(
            table_names=lambda: list(descriptions),
            get_table_description=lambda cursor, name: list(descriptions[name]),
            get_constraints=lambda cursor, name: {'pk': {'primary_key': True}})
        connection = SimpleNamespace(introspection=introspection, cursor=lambda: nullcontext(None))
        tree = ast.parse((ROOT / 'fingerprint.py').read_text())
        methods = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'read_schema']
        scope = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<schema-probe>', 'exec'), scope)
        self.assertIn('read_schema', scope, 'All database tables need a separate schema probe')
        original = scope['read_schema'](connection)
        self.assertEqual(set(original), set(descriptions))
        for name in descriptions:
            descriptions[name].append('changed-column')
            self.assertNotEqual(scope['read_schema'](connection), original)
            descriptions[name].pop()
        descriptions['new_table'] = ['new-column']
        self.assertNotEqual(scope['read_schema'](connection), original)

    def test_user_session_ttl_refresh_does_not_hide_identity_or_revocation_changes(self):
        from types import SimpleNamespace
        tree = ast.parse((ROOT / 'fingerprint.py').read_text())
        methods = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'normalized_rows']
        scope = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<session-normalization>', 'exec'), scope)
        self.assertIn('normalized_rows', scope)
        user = type('User', (), {'_meta': SimpleNamespace(app_label='account')})
        before = [{'id': 1, 'token_expired': 200, 'access_token': 'a' * 32, 'is_supper': False, 'is_active': True}]
        normalize = scope['normalized_rows']
        original = normalize(user, before, 100)
        self.assertEqual(normalize(user, [dict(before[0], token_expired=300)], 100), original)
        for change in ({'token_expired': 0}, {'token_expired': 99}, {'access_token': 'new'}, {'is_supper': True}):
            self.assertNotEqual(normalize(user, [dict(before[0], **change)], 100), original)
        self.assertEqual(before[0]['token_expired'], 200)
        other = type('Other', (), {'_meta': SimpleNamespace(app_label='account')})
        self.assertEqual(normalize(other, before, 100), before)
        for change in ({'is_active': False}, {'access_token': ''}, {'access_token': 'bad'}):
            row = dict(before[0], **change)
            self.assertNotEqual(normalize(user, [row], 100), normalize(user, [dict(row, token_expired=300)], 100))


if __name__ == '__main__':
    unittest.main()
