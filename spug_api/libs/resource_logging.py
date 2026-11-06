"""Append-only audit transport independent of a rejected database transaction."""
from logging.handlers import WatchedFileHandler
import os


class ResourceAuditHandler(WatchedFileHandler):
    def __init__(self, filename, **kwargs):
        kwargs.setdefault('encoding', 'utf-8')
        kwargs.setdefault('delay', True)
        super().__init__(filename, **kwargs)

    def _open(self):
        os.makedirs(os.path.dirname(self.baseFilename), mode=0o700, exist_ok=True)
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(self.baseFilename, flags, 0o600)
        return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)
