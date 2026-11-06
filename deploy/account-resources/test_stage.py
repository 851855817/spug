import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('resource_stage_tested', HERE / 'stage.py')
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)


class StageFactsTests(unittest.TestCase):
    def facts(self):
        names = ('nginx', 'spug-api', 'spug-ws', 'spug-worker', 'spug-monitor',
                 'spug-panel-worker', 'spug-ops-worker', 'spug-scheduler')
        programs = {name: {'autostart': True, 'autorestart': True} for name in (*names, 'redis')}
        release = {'image_api_updates': {'a.py': 'b' * 64}, 'rollback_image_api_updates': {'b.py': 'c' * 64}}
        baseline = {'image_id': stage.contract.BASELINE['image_id'], 'api': {'a.py': 'a' * 64}}
        candidate = {'image_id': 'sha256:' + '1' * 64, 'api': {'a.py': 'b' * 64}, 'programs': copy.deepcopy(programs),
                     'entrypoint': {'updatedb': 0, 'migrate': 1, 'hold': 0},
                     'audit_path': '/data/logs/resource-access.log', 'models': sorted(stage.contract.TABLES)}
        rollback = {'image_id': 'sha256:' + '2' * 64, 'api': dict(baseline['api'], **release['rollback_image_api_updates']),
                    'programs': {name: {'autostart': name == 'redis', 'autorestart': name == 'redis'} for name in programs},
                    'entrypoint': {'updatedb': 0, 'migrate': 0, 'hold': 1}}
        return release, {'baseline': baseline, 'candidate': candidate, 'rollback': rollback}

    def test_real_image_tree_delta_and_closed_startup_facts_are_required(self):
        self.assertTrue(hasattr(stage, 'validate_image_facts'), 'Missing image fact validation')
        release, facts = self.facts()
        stage.validate_image_facts(release, facts)
        for mode in ('candidate-file', 'rollback-file', 'old-worker', 'startup-migration', 'audit', 'model'):
            changed = copy.deepcopy(facts)
            if mode == 'candidate-file': changed['candidate']['api']['extra.py'] = 'f' * 64
            if mode == 'rollback-file': changed['rollback']['api']['a.py'] = 'f' * 64
            if mode == 'old-worker': changed['rollback']['programs']['spug-worker']['autostart'] = True
            if mode == 'startup-migration': changed['rollback']['entrypoint']['migrate'] = 1
            if mode == 'audit': changed['candidate']['audit_path'] = '/opt/temporary.log'
            if mode == 'model': changed['candidate']['models'].pop()
            with self.subTest(mode=mode), self.assertRaises(ValueError): stage.validate_image_facts(release, changed)

    def test_materialized_copy_tree_rejects_extra_missing_and_wrong_bytes(self):
        self.assertTrue(hasattr(stage, 'materialize'), 'Missing exact image source materialization')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source/manage.py'
            source.parent.mkdir()
            source.write_bytes(b'known')
            expected = {'manage.py': stage.sha(b'known')}
            stage.materialize(root / 'image', expected, lambda name: source)
            self.assertEqual(stage.tree(root / 'image'), expected)
            source.write_bytes(b'tampered')
            with self.assertRaises(ValueError): stage.materialize(root / 'other', expected, lambda name: source)

    def test_source_validation_rejects_tampering_even_with_a_new_tree_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'apps/account/models.py'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'approved')
            expected = {'apps/account/models.py': stage.sha(b'approved')}
            stage.validate_source(root, expected)
            path.write_bytes(b'changed')
            # A matching stage-files.json cannot alter the external scope's hashes.
            with self.assertRaises(ValueError): stage.validate_source(root, expected)

    def test_materialize_preserves_exact_existing_migration_tool_without_opening_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source/tools/migrate.py'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'existing migration utility')
            expected = {'tools/migrate.py': stage.sha(source.read_bytes())}
            stage.materialize(root / 'image', expected, lambda name: source)
            self.assertEqual(stage.tree(root / 'image'), expected)
            for index, name in enumerate(('tools/other.py', 'tools/migrate.json', 'tools/../migrate.py', '/tools/migrate.py')):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'image-source-path-invalid'):
                    stage.materialize(root / ('rejected-' + str(index)), {name: expected['tools/migrate.py']}, lambda name: source)

    def test_image_updates_allow_the_same_reviewed_migration_in_api_and_history(self):
        self.assertEqual(stage.image_updates({'old.py': 'a'}, {'new.py': 'b'}, {'new.py': 'b'}),
                         {'old.py': 'a', 'new.py': 'b'})


if __name__ == '__main__': unittest.main()
