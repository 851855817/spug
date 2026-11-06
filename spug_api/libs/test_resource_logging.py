import logging
from pathlib import Path
import tempfile
from unittest import TestCase


class ResourceAuditLoggingTests(TestCase):
    def test_denial_metadata_survives_independent_handler_reopen(self):
        from libs.resource_logging import ResourceAuditHandler
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audit' / 'resource-access.log'
            event = logging.LogRecord('test-resource-audit', logging.WARNING, '', 0,
                                      'Resource access rejected: %s', ('{"actor_id":2,"event":"quota_denied"}',), None)
            for _ in range(2):
                handler = ResourceAuditHandler(str(path))
                handler.emit(event)
                handler.close()
            self.assertEqual(path.read_text().count('quota_denied'), 2)
