"""Parent-owned authorization for disposable, database-free collectors.

The child must wait for a fresh affirmative response before each network action.
Only the parent queries account state. No database credentials or authorization
snapshots are sent to the child, and revocation terminates/reaps the process.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import queue
import subprocess
import sys
import threading
import time


_parent = ContextVar('collector_parent_authorizer', default=None)
_child = ContextVar('collector_child_authorization', default=False)


@contextmanager
def guard(authorize):
    token = _parent.set(authorize)
    try:
        authorize()
        yield
    finally:
        _parent.reset(token)


def current_authorizer():
    return _parent.get()


def configure_child(payload):
    _child.set(payload.pop('_parent_authorized', False) is True)


def authorize_remote():
    if _parent.get() is not None:
        _parent.get()()
        return
    if not _child.get():
        return
    sys.stdout.buffer.write(b'{"request":"authorize"}\n')
    sys.stdout.buffer.flush()
    answer = sys.stdin.buffer.readline(128)
    if answer != b'{"authorized":true}\n':
        raise PermissionError('Collector authorization revoked')


def exchange(child, payload, authorize, timeout, maximum):
    deadline = time.monotonic() + timeout
    received, stopped = queue.Queue(maxsize=1), threading.Event()

    def read():
        try:
            while not stopped.is_set():
                data = child.stdout.readline(maximum + 1)
                while not stopped.is_set():
                    try:
                        received.put(data, timeout=.1)
                        break
                    except queue.Full:
                        continue
                if not data or len(data) > maximum:
                    break
        except (OSError, ValueError):
            if not stopped.is_set():
                try:
                    received.put_nowait(b'')
                except queue.Full:
                    pass

    reader = threading.Thread(target=read, daemon=True)
    outgoing = queue.Queue(maxsize=1)
    writer = None
    def write():
        while not stopped.is_set():
            try:
                encoded, done, errors = outgoing.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                child.stdin.write(encoded)
                child.stdin.flush()
            except (OSError, ValueError) as error:
                errors.append(error)
            finally:
                done.set()

    def send(encoded):
        done, errors = threading.Event(), []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('collector', timeout)
        try:
            outgoing.put((encoded, done, errors), timeout=remaining)
        except queue.Full:
            raise subprocess.TimeoutExpired('collector', timeout) from None
        if not done.wait(timeout=max(0, deadline - time.monotonic())):
            raise subprocess.TimeoutExpired('collector', timeout)
        if errors:
            raise ValueError('Collector input failed') from None

    try:
        authorize()
        wire = {**payload, '_parent_authorized': True}
        encoded = json.dumps(wire, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        reader.start()
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        send(encoded)
        # A bounded reader may make up to 200 inventory requests plus reads.
        for _ in range(16384):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('collector', timeout)
            try:
                data = received.get(timeout=remaining)
            except queue.Empty:
                raise subprocess.TimeoutExpired('collector', timeout) from None
            if not data or len(data) > maximum:
                raise ValueError('Invalid collector output')
            try:
                frame = json.loads(data)
            except ValueError:
                raise ValueError('Invalid collector output') from None
            if frame == {'request': 'authorize'}:
                authorize()
                send(b'{"authorized":true}\n')
                continue
            child.stdin.close()
            child.wait(timeout=max(.01, deadline - time.monotonic()))
            if child.returncode != 0:
                raise ValueError('Collector failed')
            authorize()
            return data
        raise ValueError('Collector authorization request limit exceeded')
    finally:
        stopped.set()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if reader.ident is not None:
            reader.join(timeout=1)
        if writer is not None and writer.ident is not None:
            writer.join(timeout=1)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()
