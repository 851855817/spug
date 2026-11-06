import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent


class ProbeTests(unittest.TestCase):
    def module(self):
        path = HERE / 'schema.py'
        self.assertTrue(path.is_file(), 'Missing resource preservation probe')
        spec = importlib.util.spec_from_file_location('resource_schema_release', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_preserves_idle_credential_guards_and_reads_additions_with_historical_orm(self):
        module = self.module()
        legacy = HERE.parent.joinpath('upgrade-review/fingerprint.py').read_text()
        probe = module.build_probe(legacy)
        compile(probe, '<resource-probe>', 'exec')
        for guard in ('assert not PanelLock.objects.exists()', 'assert not Task.objects.filter(is_active=True).exists()',
                      'assert ssl_idle(apps, timezone.now())', "result['credentials_decrypt'] = True"):
            self.assertIn(guard, probe)
        self.assertIn('loader.project_state().apps.get_models()', probe)
        self.assertIn('resource_envelope(result)', probe)
        for app in ('account', 'alarm', 'deploy', 'notify'):
            self.assertIn(app + '/0002_account_resources', probe)
        with self.assertRaises(ValueError):
            module.build_probe(legacy.replace('assert table in tables', 'assert table not in tables'))


if __name__ == '__main__': unittest.main()
