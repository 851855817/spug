"""Real child-process timeout/failure tests; no Docker or customer API access."""
import gzip
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from release_safety import write_database_backup

PYTHON = getattr(sys, '_base_executable', sys.executable)


class DatabaseBackupTests(unittest.TestCase):
    def test_hanging_dump_is_killed_and_private_plaintext_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                write_database_backup([PYTHON, '-c', 'import time; time.sleep(30)'], root, timeout=0.2)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(list(root.iterdir()), [])

    def test_failed_dump_does_not_leave_a_usable_backup_or_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(subprocess.CalledProcessError):
                write_database_backup([PYTHON, '-c', 'import sys; print("partial"); sys.exit(1)'], root)
            self.assertEqual(list(root.iterdir()), [])

    def test_successful_dump_is_compressed_without_plaintext_residue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_database_backup([PYTHON, '-c', 'print("test fixture SQL")'], root)
            with gzip.open(root / 'database.sql.gz', 'rt') as stream:
                self.assertEqual(stream.read().strip(), 'test fixture SQL')
            self.assertFalse((root / 'database.sql.raw').exists())


if __name__ == '__main__':
    unittest.main()
