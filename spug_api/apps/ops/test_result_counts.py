from django.test import SimpleTestCase
from types import SimpleNamespace

from .service import run_view


class TargetCollection:
    def __init__(self, targets):
        self.targets = targets

    def all(self):
        return self.targets


class ResultCountsTests(SimpleTestCase):
    def run_with(self, targets):
        return SimpleNamespace(
            id=7, job='linux_inspection', job_name='Linux 基础巡检', version='1', params={}, state='paused',
            batch_size=1, failure_threshold=1, batch_done=1, batch_failures=0, cancel_requested=False,
            created_at=None, updated_at=None, created_by_id=1, targets=TargetCollection(targets),
        )

    def test_list_counts_include_current_check_warnings_and_unknowns_without_exposing_checks(self):
        targets = [
            SimpleNamespace(state='success', checks=[{'status': 'ok'}, {'status': 'warning'}, {'status': 'unknown'}]),
            SimpleNamespace(state='success', checks=[{'status': 'warning'}, {'status': 'unknown'}]),
            SimpleNamespace(state='failed', checks=[]), SimpleNamespace(state='unknown', checks=[]),
            SimpleNamespace(state='pending', checks=[]),
        ]
        result = run_view(self.run_with(targets), detail=False)
        self.assertIn('warning_checks', result['counts'])
        self.assertEqual(result['counts']['warning_checks'], 2)
        self.assertEqual(result['counts']['unknown_checks'], 2)
        self.assertEqual(result['counts']['success'], 2)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['counts']['unknown'], 1)
        self.assertEqual(result['counts']['total'], 5)
        self.assertNotIn('targets', result)

    def test_valid_empty_check_collection_reports_explicit_zero_counts(self):
        result = run_view(self.run_with([SimpleNamespace(state='pending', checks=[])]), detail=False)
        self.assertIn('warning_checks', result['counts'])
        self.assertEqual(result['counts']['warning_checks'], 0)
        self.assertEqual(result['counts']['unknown_checks'], 0)
