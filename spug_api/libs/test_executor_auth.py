import io
import sys
import time
import subprocess
import os
import threading
from unittest import TestCase
from unittest.mock import Mock

from libs.executor import Executor


class ExecutorAuthorizationTests(TestCase):
    def process(self, script):
        task = subprocess.Popen([sys.executable, '-u', '-c', script], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, start_new_session=os.name == 'posix')
        executor = Executor(authorize=lambda: None)
        executor.task = task
        self.addCleanup(executor._stop)
        return executor

    def test_consecutive_commands_keep_one_reader_and_live_shell(self):
        executor = self.process("import sys,os\nfor line in sys.stdin:\n"
            " if line.startswith('echo Spug EOF'): os.write(1,b'Spug EOF 2108111926 0\\n')\n"
            " elif line.strip(): os.write(1,(line.strip().upper()+'\\n').encode())\n")
        self.assertEqual(executor.exec_command('first'), (0, 'FIRST\r\n'))
        self.assertIsNone(executor.task.poll())
        self.assertEqual(executor.exec_command('second'), (0, 'SECOND\r\n'))
        self.assertIsNone(executor.task.poll())

    def test_long_line_preserves_eof_marker_at_buffer_boundary(self):
        executor = self.process("import sys,os\nfor line in sys.stdin:\n"
            " if line.startswith('echo Spug EOF'): os.write(1,b'Spug EOF 2108111926 0\\n')\n"
            " elif line.strip(): os.write(1,b'x'*65530)\n")
        deadline = time.monotonic() + 2
        def authorize():
            if time.monotonic() >= deadline:
                raise AssertionError('EOF marker was lost at a buffer boundary')
        executor.authorize = authorize
        self.assertEqual(executor.exec_command('fixture'), (0, 'x' * 65530))
        self.assertIsNone(executor.task.poll())

    def test_long_utf8_line_preserves_characters_at_buffer_boundary(self):
        executor = self.process("import sys,os\nfor line in sys.stdin:\n"
            " if line.startswith('echo Spug EOF'): os.write(1,b'Spug EOF 2108111926 0\\n')\n"
            " elif line.strip(): os.write(1,('\\u4e2d'*23000+'\\n').encode())\n")
        self.assertEqual(executor.exec_command('fixture'), (0, '中' * 23000 + '\r\n'))

    def test_external_stop_ends_a_waiting_command(self):
        executor = self.process('import time;time.sleep(20)')
        done = threading.Event()
        errors = []
        def execute():
            try:
                executor.exec_command('fixture')
            except (RuntimeError, OSError) as exc:
                errors.append(exc)
            finally:
                done.set()
        worker = threading.Thread(target=execute, daemon=True)
        worker.start()
        time.sleep(.1)
        executor._stop()
        self.assertTrue(done.wait(2), 'external stop left the command waiting for output')
        worker.join(1)

    def test_revocation_interrupts_stdin_backpressure(self):
        executor = self.process("import sys,time\nsys.stdout.write('x'*2000000);sys.stdout.flush();time.sleep(20)")
        started = time.monotonic()
        def authorize():
            if time.monotonic() - started >= .2:
                raise PermissionError('revoked')
        executor.authorize = authorize
        done = threading.Event()
        errors = []
        def execute():
            try:
                executor.exec_command('x' * 2000000)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        worker = threading.Thread(target=execute, daemon=True)
        worker.start()
        stopped = done.wait(2)
        if not stopped:
            executor._stop()
            worker.join(2)
        self.assertTrue(stopped, 'authorization polling stopped while stdin was blocked')
        self.assertTrue(any(isinstance(exc, PermissionError) for exc in errors), errors)
        self.assertIsNotNone(executor.task.poll())
    def test_local_process_is_killed_when_authorization_is_revoked_while_idle(self):
        from libs.executor import run_authorized
        started = time.monotonic()
        def authorize():
            if time.monotonic() - started > .2:
                raise PermissionError('revoked')
        with self.assertRaises(PermissionError):
            run_authorized([sys.executable, '-c', 'import time; time.sleep(20)'], authorize, timeout=3)
        self.assertLess(time.monotonic() - started, 2)

    def test_revoked_local_dispatch_never_writes_a_command(self):
        def authorize():
            raise PermissionError('revoked')
        executor = Executor(authorize=authorize)
        executor.task = Mock(stdin=io.BytesIO())
        with self.assertRaises(PermissionError):
            list(executor.exec_command_with_stream('rsync fixture'))
        self.assertEqual(executor.task.stdin.getvalue(), b'')

    def test_revocation_during_output_does_not_release_output(self):
        active = [True]
        def authorize():
            if not active[0]:
                raise PermissionError('revoked')
        def read(_):
            active[0] = False
            return b'secret'
        executor = Executor(authorize=authorize)
        executor.task = Mock(stdin=io.BytesIO(), stdout=Mock(read=read))
        with self.assertRaises(PermissionError):
            list(executor.exec_command_with_stream('rsync fixture'))
