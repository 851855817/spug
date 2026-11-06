import importlib.util
import copy
import ast
import hashlib
import json
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


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

    def column(self):
        field = namedtuple('FieldInfo', 'name type_code display_size internal_size precision scale null_ok default collation extra is_unsigned has_json_constraint comment')
        return field('id', 8, 0, 20, 19, 0, 0, None, None, 'auto_increment', 0, False, '')

    def test_addition_structure_ignores_only_sample_row_display_size(self):
        module = self.module()
        empty, populated = self.column(), self.column()
        populated = populated._replace(display_size=1)
        constraints = {'PRIMARY': {'columns': ['id'], 'primary_key': True}}
        expected = module.addition_schema([empty], constraints)
        self.assertEqual(expected, module.addition_schema([populated], constraints))
        for index in range(len(empty)):
            if index == 2: continue
            changed = populated._replace(**{populated._fields[index]: 'schema-drift'})
            with self.subTest(index=index): self.assertNotEqual(expected, module.addition_schema([changed], constraints))
        self.assertNotEqual(expected, module.addition_schema([populated], {}))
        with self.assertRaises(AssertionError): module.addition_schema([tuple(populated) + ('unknown-field',)], constraints)

    def test_embedded_probe_keeps_old_schema_exact_and_stabilizes_only_new_tables(self):
        module = self.module()
        probe = module.build_probe(HERE.parent.joinpath('upgrade-review/fingerprint.py').read_text())
        functions = [node for node in ast.parse(probe).body if isinstance(node, ast.FunctionDef)
                     and node.name in ('addition_schema', 'resource_envelope')]
        new, old = 'account_resource_policies', 'users'
        cursor = mock.MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        column = self.column()
        column = column._replace(display_size=1)
        constraints = {'PRIMARY': {'columns': ['id'], 'primary_key': True}}
        introspection = SimpleNamespace(table_names=lambda: [new, old],
            get_table_description=lambda cursor, table: [copy.deepcopy(column)],
            get_constraints=lambda cursor, table: copy.deepcopy(constraints))
        connection = SimpleNamespace(cursor=lambda: cursor, introspection=introspection)
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
        state = SimpleNamespace(apps=SimpleNamespace(get_models=lambda: []))
        context = {'connection': connection, 'RESOURCE_ADDITIONS': (new,), 'digest': digest,
                   'MigrationExecutor': lambda connection: SimpleNamespace(loader=SimpleNamespace(project_state=lambda: state))}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<probe-functions>', 'exec'), context)
        result = context['resource_envelope']({})
        self.assertEqual(result['schema'][new], digest(module.addition_schema([column], constraints)))
        self.assertEqual(result['schema'][old], digest({'columns': [column], 'constraints': constraints}))


if __name__ == '__main__': unittest.main()
