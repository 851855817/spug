"""A failed renewal preview has no work to dispatch; every ambiguous case blocks."""
import ast
import copy
from pathlib import Path
import unittest


class FailedPreflightIdleTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name('fingerprint.py').read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == 'failed_renewal_preflight']
        scope = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<idle-guard>', 'exec'), scope)
        self.assertIn('failed_renewal_preflight', scope)
        self.is_idle = scope['failed_renewal_preflight']
        self.row = {'state': 'prepared', 'reconcile_requested': False, 'task__state': 'prepared',
                    'task__operation': 'ssl_renew', 'task__audit': [], 'children': [
                        {'state': 'failed', 'snapshot': {'state': 'failed'},
                         'result': {'state': 'failed'}, 'intents': [], 'resolutions': []}]}

    def test_failed_frozen_renewal_preview_is_idle_without_modifying_it(self):
        original = copy.deepcopy(self.row)
        self.assertTrue(self.is_idle(self.row))
        self.assertEqual(self.row, original)

    def test_other_or_executable_work_and_incomplete_history_always_block(self):
        changes = [
            {'state': 'running'}, {'state': 'preflight_queued'}, {'reconcile_requested': True},
            {'task__state': 'running'}, {'task__operation': 'file_upload'},
            {'task__audit': [{'action': 'start'}]}, {'children': []}, {'children': None},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(self.is_idle(dict(self.row, **change)))
        for change in ({'state': 'ready'}, {'snapshot': {'state': 'ready'}}, {'snapshot': {}},
                       {'result': {}}, {'result': {'state': 'failed', 'remote': {}}},
                       {'intents': [{'phase': 'apply'}]}, {'intents': None},
                       {'resolutions': [{'state': 'failed'}]}, {'resolutions': None}):
            with self.subTest(child=change):
                child = dict(self.row['children'][0], **change)
                self.assertFalse(self.is_idle(dict(self.row, children=[child])))
        for key in self.row:
            row = dict(self.row); del row[key]
            with self.subTest(missing=key): self.assertFalse(self.is_idle(row))
        for key in self.row['children'][0]:
            child = dict(self.row['children'][0]); del child[key]
            with self.subTest(missing_child=key):
                self.assertFalse(self.is_idle(dict(self.row, children=[child])))

    def test_one_ready_child_in_a_failed_preview_still_blocks(self):
        self.row['children'].append(dict(self.row['children'][0], state='ready', snapshot={'state': 'ready'}))
        self.assertFalse(self.is_idle(self.row))
