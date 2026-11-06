import os
import signal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.baota.management.commands.runpanelworker import ProcessLock
from apps.ops.worker import OpsWorker, recover_interrupted


class Command(BaseCommand):
    help = 'Run the durable read-only operations worker (one SSH target at a time)'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--lock-file', default=getattr(settings, 'OPS_WORKER_LOCK_FILE',
                                                          '/data/spug/ops-worker.lock' if os.name != 'nt'
                                                          else str(Path.cwd() / '.ops-worker.lock')))

    def handle(self, *args, **options):
        worker = OpsWorker()
        with ProcessLock(options['lock_file']):
            recover_interrupted()
            if options['once']:
                worker.run_once()
                return
            previous = {}
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, lambda *_: worker.stop_event.set())
            try:
                worker.run()
            except Exception:
                raise CommandError('运维 worker 中断；请重启，已下发目标将保留为待核实，不会自动重发') from None
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
