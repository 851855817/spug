"""Publication must prove append-only DDL, not merely ignore schema changes."""
import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('ssl_release_schema', Path(__file__).with_name('schema.py'))
schema = importlib.util.module_from_spec(spec)
if spec.loader is not None:
    spec.loader.exec_module(schema)


class AdditiveSchemaTests(unittest.TestCase):
    def setUp(self):
        empty = {'count': 0, 'digest': 'e' * 64}
        self.before = {
            'business': {'hosts': {'count': 1, 'digest': 'a' * 64},
                         'schema_sha256': 'b' * 64, 'migration_rows_sha256': 'c' * 64,
                         'credentials_decrypt': True, 'baota.key': 'd' * 64},
            'schema': {'hosts': 'a' * 64, 'django_migrations': 'b' * 64},
            'migrations': {'monitor/0001_initial': 'c' * 64},
        }
        self.after = copy.deepcopy(self.before)
        self.after['business'].update(schema_sha256='f' * 64, migration_rows_sha256='f' * 64)
        for table in schema.SSL_TABLES:
            self.after['schema'][table] = 'e' * 64
            self.after['business'][table] = dict(empty)
        self.after['migrations']['monitor/0002_ssl_monitor'] = 'f' * 64

    def verify(self):
        schema.verify_additive(self.before, self.after, '0002_ssl_monitor')

    def test_exact_empty_additions_with_preserved_history_are_accepted(self):
        self.verify()

    def test_changed_old_row_is_refused(self):
        self.after['business']['hosts']['digest'] = '0' * 64
        with self.assertRaises(schema.SchemaFailure):
            self.verify()

    def test_changed_old_schema_is_refused(self):
        self.after['schema']['hosts'] = '0' * 64
        with self.assertRaises(schema.SchemaFailure):
            self.verify()

    def test_missing_extra_or_nonempty_table_is_refused(self):
        table = schema.SSL_TABLES[0]
        for mode in ('missing', 'extra', 'nonempty'):
            with self.subTest(mode=mode):
                after = copy.deepcopy(self.after)
                if mode == 'missing':
                    del after['schema'][table]
                elif mode == 'extra':
                    after['schema']['unreviewed'] = 'e' * 64
                else:
                    after['business'][table]['count'] = 1
                with self.assertRaises(schema.SchemaFailure):
                    schema.verify_additive(self.before, after, '0002_ssl_monitor')

    def test_unrecorded_extra_or_rewritten_migration_is_refused(self):
        for mode in ('missing', 'extra', 'rewrite'):
            with self.subTest(mode=mode):
                after = copy.deepcopy(self.after)
                if mode == 'missing':
                    del after['migrations']['monitor/0002_ssl_monitor']
                elif mode == 'extra':
                    after['migrations']['other/0003_secret'] = '0' * 64
                else:
                    after['migrations']['monitor/0001_initial'] = '0' * 64
                with self.assertRaises(schema.SchemaFailure):
                    schema.verify_additive(self.before, after, '0002_ssl_monitor')

    def test_existing_ssl_schema_refuses_first_publication(self):
        self.before['schema'][schema.SSL_TABLES[0]] = 'e' * 64
        with self.assertRaises(schema.SchemaFailure):
            self.verify()

    def test_probe_preserves_all_idle_guards_and_syntax(self):
        legacy = Path(__file__).parents[1].joinpath('upgrade-review/fingerprint.py').read_text(encoding='utf-8')
        probe = schema.build_probe(legacy, '0002_ssl_monitor')
        compile(probe, '<ssl-preservation-probe>', 'exec')
        self.assertIn("assert not PanelLock.objects.exists()", probe)
        self.assertIn("assert not Task.objects.filter(is_active=True).exists()", probe)
        self.assertIn('ssl_envelope(result)', probe)
        with self.assertRaises(schema.SchemaFailure):
            schema.build_probe(legacy.replace('assert table in tables', 'assert table not in tables'), '0002_ssl_monitor')


if __name__ == '__main__':
    unittest.main()
