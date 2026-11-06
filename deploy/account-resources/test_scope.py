import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


class ScopeTests(unittest.TestCase):
    def module(self):
        path = HERE / 'scope.py'
        self.assertTrue(path.is_file(), 'Missing exact candidate scope/hash validator')
        spec = importlib.util.spec_from_file_location('account_release_scope', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_runtime_classification_covers_cross_app_dependencies_without_test_fixtures(self):
        scope = self.module()
        for name in ('spug_api/libs/resource_logging.py', 'spug_api/consumer/consumers.py',
                     'spug_api/apps/account/resource_model_contract.json', 'spug_web/src/resources/access.js'):
            self.assertTrue(scope.runtime_file(name), name)
        for name in ('spug_api/apps/account/test_resources.py', 'spug_api/apps/account/resource_test_urls.py',
                     'spug_api/spug/mariadb_test_settings.py', 'spug_web/src/resources/access.test.js',
                     'docs/guide.md'):
            self.assertFalse(scope.runtime_file(name), name)

    def test_freeze_uses_two_real_revisions_exact_names_and_both_versions_of_bytes(self):
        scope = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root).decode().strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            path = root / 'spug_api/libs/executor.py'
            path.parent.mkdir(parents=True)
            path.write_text('old\n')
            git('add', '.')
            git('commit', '-qm', 'baseline')
            previous = git('rev-parse', 'HEAD')
            path.write_text('new\n')
            git('add', '.')
            git('commit', '-qm', 'candidate')
            revision = git('rev-parse', 'HEAD')
            reviewed = {'profile': 'account-resources-v1', 'previous_revision': previous,
                        'runtime_paths': ['spug_api/libs/executor.py']}
            result = scope.freeze(root, reviewed)
            self.assertEqual(result['revision'], revision)
            self.assertEqual(result['previous_revision'], previous)
            self.assertNotEqual(result['files']['spug_api/libs/executor.py']['previous_sha256'],
                                result['files']['spug_api/libs/executor.py']['sha256'])
            reviewed['runtime_paths'].append('spug_api/libs/channel.py')
            with self.assertRaises(ValueError): scope.freeze(root, reviewed)
            reviewed['runtime_paths'].pop()
            path.write_text('uncommitted\n')
            with self.assertRaises(ValueError): scope.freeze(root, reviewed)

    def test_untracked_runtime_and_deletion_fail_closed(self):
        scope = self.module()
        with self.assertRaises(ValueError):
            scope.validate_names({'spug_api/libs/executor.py'}, {'spug_api/libs/executor.py', 'spug_api/consumer/consumers.py'})


if __name__ == '__main__':
    unittest.main()
