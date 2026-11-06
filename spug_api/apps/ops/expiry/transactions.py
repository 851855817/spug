"""Retry known concurrent changes only after the complete transaction rolls back."""
from functools import wraps
from time import sleep

from django.db import OperationalError, connection


class InventoryChanged(RuntimeError):
    """Canonical assets changed between discovery and acquiring their locks."""


def retry_lock_conflicts(action):
    @wraps(action)
    def wrapped(*args, **kwargs):
        # A caller-owned transaction cannot safely be restarted here.
        attempts = 1 if connection.in_atomic_block else 3
        for attempt in range(attempts):
            try:
                return action(*args, **kwargs)
            except (OperationalError, InventoryChanged) as error:
                retryable = isinstance(error, InventoryChanged) or bool(
                    error.args and error.args[0] in (1205, 1213, 3572))
                if not retryable or attempt + 1 == attempts:
                    raise
                sleep(0.05 * (attempt + 1))
    return wrapped
