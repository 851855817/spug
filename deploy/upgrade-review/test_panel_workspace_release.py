"""Consolidation changes only five UI files and preserves the SSL baseline."""
import ast
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from test_branding_cleanup_release import BrandingCleanupReleaseTests as _Base


class PanelWorkspaceReleaseTests(_Base):
    profile = 'panel-workspace-ui'
    ui = {'spug_web/src/routes.js', 'spug_web/src/pages/exec/panel/index.js',
          'spug_web/src/pages/exec/panel/Workspace.js', 'spug_web/src/pages/exec/operations/index.js',
          'spug_web/src/pages/exec/panel/TaskDetail.js'}
    ui_count = 5

    def test_navigation_has_one_panel_entry_and_hidden_legacy_operations_route(self):
        routes = (Path(__file__).parents[2] / 'spug_web/src/routes.js').read_text(encoding='utf8')
        # The compatibility route remains hidden; its new permission allows an
        # authorized operator to open their own scoped historical results.
        self.assertIn("{hidden: true, auth: 'ops.job.view', path: '/exec/operations', component: LegacyOperations}", routes)
        self.assertNotIn("title: t('运维作业')", routes)
        self.assertIn("auth: 'exec.panel.view|ops.job.view', path: '/exec/panel'", routes)

    def test_ssl_pending_work_and_leases_block_without_modifying_state(self):
        tree = ast.parse(Path(__file__).with_name('fingerprint.py').read_text(encoding='utf8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'ssl_idle']
        self.assertEqual(len(functions), 1)
        scope = {'timedelta': timedelta}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<ssl-idle>', 'exec'), scope)
        for job_busy, lease_busy in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(job_busy=job_busy, lease_busy=lease_busy):
                job, lease, source, registry = mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), mock.Mock()
                registry.get_model.side_effect = [job, lease, source]
                job.objects.filter.return_value.exists.return_value = job_busy
                lease.objects.filter.return_value.exclude.return_value.exists.return_value = lease_busy
                now = datetime.now(timezone.utc)
                self.assertEqual(scope['ssl_idle'](registry, now), not (job_busy or lease_busy))
                job.objects.filter.assert_any_call(state__in=('queued', 'running', 'prepared', 'uncertain'))
                for manager in (job.objects, lease.objects):
                    manager.update.assert_not_called()
                    manager.delete.assert_not_called()
                if not job_busy:
                    lease.objects.filter.assert_called_once_with(expires_at__gt=now)
                    lease.objects.filter.return_value.exclude.assert_called_once_with(token='')
        registry = mock.Mock(); registry.get_model.side_effect = LookupError()
        self.assertTrue(scope['ssl_idle'](registry, 'now'))

    def test_due_sources_block_release_without_changing_switches(self):
        tree = ast.parse(Path(__file__).with_name('fingerprint.py').read_text(encoding='utf8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'ssl_idle']
        scope = {'timedelta': timedelta}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<ssl-idle>', 'exec'), scope)
        now = datetime.now(timezone.utc)
        for attempt, expected in ((None, False), (now - timedelta(hours=7), False),
                                  (now - timedelta(hours=6) + timedelta(minutes=10), False), (now, True)):
            job, lease, source, registry = mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), mock.Mock()
            registry.get_model.side_effect = [job, lease, source]
            job.objects.filter.return_value.exists.return_value = False
            lease.objects.filter.return_value.exclude.return_value.exists.return_value = False
            source.objects.filter.return_value = [SimpleNamespace(last_attempt_at=attempt, interval_hours=6)]
            self.assertEqual(scope['ssl_idle'](registry, now), expected)
            source.objects.filter.assert_called_once_with(enabled=True)
            source.objects.update.assert_not_called()

    def test_only_released_expired_worker_timestamp_is_normalized(self):
        tree = ast.parse(Path(__file__).with_name('fingerprint.py').read_text(encoding='utf8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'normalized_rows']
        scope = {'datetime': datetime}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<ssl-rows>', 'exec'), scope)
        model = SimpleNamespace(_meta=SimpleNamespace(app_label='monitor'), __name__='SSLLease')
        now = datetime.now(timezone.utc)
        row = {'key': 'worker', 'token': '', 'job_id': None, 'expires_at': now - timedelta(seconds=1)}
        first = scope['normalized_rows'](model, [row], now.timestamp())
        second = scope['normalized_rows'](model, [dict(row, expires_at=now - timedelta(seconds=10))], now.timestamp())
        self.assertEqual(first, second)
        self.assertIsInstance(row['expires_at'], datetime)
        for changed in (dict(row, token='active'), dict(row, job_id=7), dict(row, key='server:1'),
                        dict(row, expires_at=now + timedelta(seconds=10))):
            self.assertEqual(scope['normalized_rows'](model, [changed], now.timestamp()), [changed])


del _Base
