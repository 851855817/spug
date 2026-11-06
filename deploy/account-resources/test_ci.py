"""CI must exercise the complete backend and the installed image, independently."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


class CompleteBackendChecks(unittest.TestCase):
    def selector(self):
        path = HERE / 'ci_tests.py'
        self.assertTrue(path.is_file(), 'Missing complete backend test discovery')
        spec = importlib.util.spec_from_file_location('resource_ci_tests', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_discovers_account_libs_and_future_consumer_tests_without_url_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('apps/account/test_quota.py', 'libs/test_executor.py', 'consumer/test_session.py'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('class Cases:\n def test_boundary(self): pass\n')
            (root / 'spug').mkdir()
            (root / 'spug/test_settings.py').write_text('DATABASES = {}\n')
            (root / 'spug/test_urls.py').write_text('urlpatterns = []\n')
            self.assertEqual(self.selector().discover(root), [
                'apps.account.test_quota', 'consumer.test_session', 'libs.test_executor'])

    def test_current_checkout_has_all_real_test_modules(self):
        modules = self.selector().discover(ROOT / 'spug_api')
        self.assertIn('apps.account.test_quota_concurrency', modules)
        self.assertIn('apps.account.test_resource_schema', modules)
        self.assertIn('libs.test_executor_auth', modules)
        self.assertIn('libs.test_resource_logging', modules)
        self.assertNotIn('spug.test_urls', modules)
        self.assertNotIn('apps.schedule.test_urls', modules)
        self.assertEqual(len(modules), len(set(modules)))

    def test_both_databases_run_discovery_and_prs_cover_all_backend_dependencies(self):
        workflow = ROOT.joinpath('.github/workflows/baota-checks.yml').read_text()
        self.assertIn("'spug_api/**'", workflow)
        self.assertIn("'deploy/account-resources/**'", workflow)
        self.assertEqual(workflow.count('python3 deploy/account-resources/ci_tests.py'), 2)
        self.assertEqual(workflow.count('KOPS_RESOURCE_AUDIT_LOG=/tmp/resource-access.log'), 2)
        self.assertIn('test_image.py account-resources', workflow)
        self.assertIn('unittest discover -s deploy/account-resources', workflow)

    def test_image_contains_the_complete_api_and_real_import_probe(self):
        dockerfile = ROOT.joinpath('deploy/baota/Dockerfile').read_text()
        self.assertIn('COPY spug_api/ /opt/spug/spug_api/', dockerfile)
        checks = ROOT.joinpath('deploy/baota/test_image.py').read_text()
        self.assertIn('class AccountResourcesImageTests', checks)
        self.assertIn('django.setup()', checks)
        self.assertIn('spug.asgi', checks)
        self.assertIn("get_resolver('spug.urls')", checks)


if __name__ == '__main__':
    unittest.main()
