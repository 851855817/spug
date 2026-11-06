from libs.utils import str_decode
import subprocess
import re
import os
import queue
import signal
import threading
import time
import tempfile
import io


def _kill(task):
    if task.poll() is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(task.pid, signal.SIGKILL)
        else:
            task.kill()
    except ProcessLookupError:
        pass
    task.wait(timeout=5)


def run_authorized(command, authorize, *, env=None, timeout=30, maximum=4 * 1024 * 1024):
    """Bounded local subprocess; authorization always runs on the calling thread."""
    authorize()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        task = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            env=env, start_new_session=os.name == 'posix')
        deadline = time.monotonic() + timeout
        try:
            while task.poll() is None:
                authorize()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > maximum:
                    raise ValueError('本地进程输出超过限制')
                time.sleep(.1)
            authorize()
            if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > maximum:
                raise ValueError('本地进程输出超过限制')
            stdout.seek(0)
            stderr.seek(0)
            return subprocess.CompletedProcess(command, task.returncode, stdout.read(), stderr.read())
        finally:
            _kill(task)


class Executor:
    def __init__(self, default_env=None, authorize=None):
        self.regex = re.compile(r'(?<!echo )Spug EOF 2108111926 (-?\d+)[\r\n]?')
        self.default_env = default_env
        self.task = None
        self.authorize = authorize
        self._received = queue.Queue(maxsize=64)
        self._reader_stopped = threading.Event()
        self._reader_eof = threading.Event()
        self._reader = self._writer = None
        self._write_done = threading.Event()
        self._write_requests = queue.Queue(maxsize=1)
        self._write_errors = []
        self._read_buffer = b''
        self._read_at = 0

    def _check(self):
        if self.authorize is not None:
            self.authorize()

    def _stop(self):
        self._reader_stopped.set()
        if self.task:
            if os.name == 'posix' and type(self.task.pid) is int:
                try:
                    os.killpg(self.task.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self.task.kill()
            self.task.wait(timeout=5)
        for worker in (self._writer, self._reader):
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=1)
        if isinstance(self.task, subprocess.Popen):
            for pipe in (self.task.stdin, self.task.stdout):
                if pipe is not None:
                    pipe.close()

    def _start_reader(self):
        if self._reader is not None:
            return
        def read():
            stream = self.task.stdout
            read_chunk = stream.read1 if isinstance(stream, io.BufferedReader) else stream.read
            try:
                while not self._reader_stopped.is_set():
                    data = read_chunk(4096 if isinstance(stream, io.IOBase) else 1)
                    while not self._reader_stopped.is_set():
                        try:
                            self._received.put(data, timeout=.1)
                            break
                        except queue.Full:
                            continue
                    if not data:
                        return
            except (OSError, ValueError):
                self._reader_eof.set()
        self._reader = threading.Thread(target=read, daemon=True)
        self._reader.start()

    def _write(self, payload):
        self._write_errors = []
        self._write_done.clear()
        def write():
            try:
                for offset in range(0, len(payload), 32768):
                    if self._reader_stopped.is_set():
                        return
                    permitted = threading.Event()
                    self._write_requests.put(permitted)
                    while not permitted.wait(.05):
                        if self._reader_stopped.is_set():
                            return
                    self.task.stdin.write(payload[offset:offset + 32768])
                    self.task.stdin.flush()
            except (OSError, ValueError) as exc:
                self._write_errors.append(exc)
            finally:
                self._write_done.set()
        self._writer = threading.Thread(target=write, daemon=True)
        self._writer.start()

    def _authorize_write(self):
        try:
            permitted = self._write_requests.get_nowait()
        except queue.Empty:
            return
        self._check()  # Database authorization stays on the calling thread.
        permitted.set()

    def _output(self):
        checked_at = 0
        while True:
            if self._reader_stopped.is_set():
                raise RuntimeError('本地命令已停止')
            if time.monotonic() - checked_at >= .1:
                self._check()
                self._authorize_write()
                checked_at = time.monotonic()
            if self._write_errors:
                raise self._write_errors[0]
            if self._read_at >= len(self._read_buffer):
                try:
                    self._read_buffer = self._received.get(timeout=.1)
                    self._read_at = 0
                except queue.Empty:
                    if self._reader_eof.is_set():
                        yield b''
                        return
                    continue
                if not self._read_buffer:
                    self._check()
                    yield b''
                    return
            data = self._read_buffer[self._read_at:self._read_at + 1]
            self._read_at += 1
            if data in (b'\r', b'\n'):
                self._check()
            yield data

    @property
    def pid(self):
        if self.task:
            return self.task.pid
        return None

    def get_envs(self):
        envs = {}
        _, output = self.exec_command('env | grep SPUG_')
        if output:
            for item in output.splitlines():
                if '=' in item:
                    key, val = item.split('=', 1)
                    envs[key] = val
        return envs

    def exec_command_with_stream(self, command):
        self._check()
        payload = (command + '\necho Spug EOF 2108111926 $?\n').encode()
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError('本地命令超过长度限制')
        self._start_reader()
        self._write(payload)
        exit_code, message = -1, bytearray()
        try:
            for output in self._output():
                if not output:
                    while self.task.poll() is None:
                        self._check()
                        time.sleep(.1)
                    exit_code = self.task.returncode
                    break
                if output in (b'\r', b'\n'):
                    message.extend(b'\r\n' if output == b'\n' else b'\r')
                    line = str_decode(bytes(message))
                    match = self.regex.search(line)
                    if match:
                        exit_code = int(match.group(1))
                        message = bytearray(line[:match.start()].encode())
                        break
                    self._check()
                    yield exit_code, line
                    message.clear()
                else:
                    message.extend(output)
                    if len(message) >= 65536:
                        # Retain the EOF frame (including its echo lookbehind and
                        # OS exit status) and any split UTF-8 character for the
                        # next chunk. Clearing the full buffer loses both.
                        boundary = len(message) - 64
                        while boundary > 0 and message[boundary] & 0xC0 == 0x80:
                            boundary -= 1
                        self._check()
                        yield exit_code, str_decode(bytes(message[:boundary]))
                        del message[:boundary]
            while not self._write_done.wait(.1):
                self._check()
                self._authorize_write()
            if self._write_errors:
                raise self._write_errors[0]
            self._check()
            yield exit_code, str_decode(bytes(message))
        except BaseException:
            # Only abandoning the command closes the shell. The persistent
            # internal reader survives ordinary EOF marker frame completion.
            self._stop()
            raise

    def exec_command(self, command):
        exit_code, message = -1, ''
        for exit_code, line in self.exec_command_with_stream(command):
            message += line
        return exit_code, message

    def __enter__(self):
        self._check()
        env = None
        if self.default_env is not None:
            env = dict(os.environ)
            env.update({k: str(v) for k, v in self.default_env.items()})
        self.task = subprocess.Popen(
            'bash',
            env=env,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == 'posix'
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop()
