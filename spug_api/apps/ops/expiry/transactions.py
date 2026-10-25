"""Retry only known rolled-back lock conflicts, outside the complete transaction."""
from functools import wraps
from time import sleep

from django.db import OperationalError, connection


def retry_lock_conflicts(action):
    @wraps(action)
    def wrapped(*args, **kwargs):
        # A caller-owned transaction cannot safely be restarted here.
        attempts = 1 if connection.in_atomic_block else 3
        for attempt in range(attempts):
            try:
                return action(*args, **kwargs)
            except OperationalError as error:
                if not error.args or error.args[0] not in (1205, 1213, 3572) or attempt + 1 == attempts:
                    raise
                sleep(0.05 * (attempt + 1))
    return wrapped
