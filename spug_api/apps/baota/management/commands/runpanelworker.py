"""Single coordinator; SIGTERM stops new claims and drains bounded in-flight calls."""
import os
import signal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.baota.worker import PanelWorker, recover_interrupted


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                if not self.stream.read(1):
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise CommandError('面板 worker 已运行或无法获取进程锁') from None
        return self

    def __exit__(self, *args):
        if os.name == 'nt':
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        # Never unlink a file lock: another process may already hold its inode.


class Command(BaseCommand):
    help = 'Run the durable Baota panel task worker (default two panels concurrently)'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Recover and process at most one queue step')
        parser.add_argument('--parallel', type=int, choices=(1, 2), default=2)
        parser.add_argument('--lock-file', default=getattr(settings, 'BAOTA_WORKER_LOCK_FILE',
                                                          '/data/spug/baota-worker.lock' if os.name != 'nt'
                                                          else str(Path.cwd() / '.baota-worker.lock')))

    def handle(self, *args, **options):
        worker = PanelWorker(max_parallel=options['parallel'])
        with ProcessLock(options['lock_file']):
            recover_interrupted()
            if options['once']:
                worker.run_once()
                return
            previous = {}
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, lambda *_: worker.stop_event.set())
            try:
                try:
                    worker.run()
                except Exception:
                    raise CommandError('面板 worker 中断；请重启，发送中的任务将保留为不确定状态') from None
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
