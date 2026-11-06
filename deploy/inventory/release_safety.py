"""Small release primitives that can be tested without production access."""
import gzip
import shutil
import subprocess


def write_database_backup(command, directory, timeout=120):
    """Bound the entire dump, including stdout reads, and erase temporary SQL."""
    plain_path = directory / 'database.sql.raw'
    archive_path = directory / 'database.sql.gz'
    completed = False
    try:
        with plain_path.open('xb') as plain:
            plain_path.chmod(0o600)
            subprocess.run(command, stdout=plain, stderr=subprocess.DEVNULL, check=True, timeout=timeout)
        with archive_path.open('xb') as raw:
            archive_path.chmod(0o600)
            with plain_path.open('rb') as plain, gzip.GzipFile(fileobj=raw, mode='wb') as zipped:
                shutil.copyfileobj(plain, zipped)
        completed = True
    finally:
        plain_path.unlink(missing_ok=True)
        if not completed:
            archive_path.unlink(missing_ok=True)
