"""Independent frozen old source contract must detect legacy field drift."""
from pathlib import Path
from unittest import TestCase


class LegacyModelContractTests(TestCase):
    def test_current_models_preserve_frozen_legacy_fields(self):
        from apps.account.resource_schema import verify_legacy_models
        verify_legacy_models(Path(__file__).resolve().parents[1])

    def test_changed_old_field_is_rejected(self):
        from apps.account.resource_schema import verify_legacy_models
        root = Path(__file__).resolve().parents[1]
        source = root.joinpath('host/models.py').read_text(encoding='utf-8')
        altered = source.replace('max_length=100', 'max_length=117', 1)
        self.assertNotEqual(source, altered)
        with self.assertRaisesRegex(ValueError, 'host/models.py'):
            verify_legacy_models(root, overrides={'host/models.py': altered})

    def test_registered_model_additions_match_nine_model_contract(self):
        import json
        from django.apps import apps
        from apps.account.resource_migration import ADDITIVE_MODELS
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(root.joinpath('account/resource_model_contract.json').read_text(encoding='utf-8'))
        old = {('apps.' + path[:-3].replace('/', '.'), name)
               for path, models in contract['models'].items() for name in models}
        current = {(model.__module__, model.__name__): (model._meta.app_label, model._meta.model_name)
                   for model in apps.get_models() if model.__module__.startswith('apps.')}
        self.assertTrue(old.issubset(current))
        self.assertEqual({current[key] for key in set(current) - old}, set(ADDITIVE_MODELS))
