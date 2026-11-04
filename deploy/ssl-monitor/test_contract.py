import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ssl_contract', Path(__file__).with_name('contract.py'))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class ContractTests(unittest.TestCase):
    def test_additive_migration_rejects_runpython_import_side_effect_and_alter(self):
        for code in (
            "from os import system\nsystem('bad')\n",
            "from django.db import migrations, models\nclass Migration(migrations.Migration):\n dependencies=[]\n operations=[migrations.RunPython(print)]\n",
            "from django.db import migrations, models\nclass Migration(migrations.Migration):\n dependencies=[]\n operations=[migrations.AlterField(model_name='detection',name='name',field=models.TextField())]\n",
        ):
            with self.subTest(code=code), self.assertRaises(ValueError):
                contract.validate_migration(code)

    def test_exact_create_model_set_is_required(self):
        code = "from django.db import migrations, models\nclass Migration(migrations.Migration):\n dependencies=[]\n operations=[migrations.CreateModel(name='Injected',fields=[])]\n"
        with self.assertRaises(ValueError): contract.validate_migration(code)

    def test_deterministic_dockerfile_copies_exact_sources(self):
        candidate, rollback = contract.dockerfiles('a' * 40, '0002_ssl_monitor')
        self.assertIn('FROM spug-baota:aaaaaaa', candidate)
        self.assertIn('apps/monitor/migrations/0002_ssl_monitor.py', candidate)
        self.assertNotIn('apps/monitor/ssl/jobs.py', rollback)
        self.assertIn('migrate --noinput', rollback)

    def test_both_restart_images_only_apply_frozen_migrations(self):
        # Both images inherit the upstream entrypoint, which otherwise calls
        # updatedb and generates new migrations on a later container restart.
        for name, dockerfile in zip(('candidate', 'rollback'),
                                   contract.dockerfiles('a' * 40, '0002_ssl_monitor')):
            with self.subTest(image=name):
                self.assertIn('python3 manage.py migrate --noinput/', dockerfile)
                self.assertIn("! grep -Eq 'updatedb|makemigrations' /entrypoint.sh", dockerfile)


if __name__ == '__main__': unittest.main()
