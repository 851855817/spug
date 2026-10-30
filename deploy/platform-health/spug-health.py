#!/usr/bin/env python3
"""Independent, read-only-by-default health checks for the fixed Spug host."""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path


ROOT = Path('/opt/spug')
CONTAINERS = ('spug', 'spug-db')
SERVICES = ('nginx', 'redis', 'spug-api', 'spug-monitor', 'spug-panel-worker',
            'spug-scheduler', 'spug-worker', 'spug-ws', 'spug-ops-worker')
TARGETS = CONTAINERS + SERVICES
STARTABLE_PROCESSES = ('STOPPED', 'EXITED', 'FATAL')
PROCESS_STATES = ('STOPPED', 'STARTING', 'RUNNING', 'BACKOFF', 'STOPPING',
                  'EXITED', 'FATAL', 'UNKNOWN')
CONTAINER_STATES = ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead')
INSPECT_FORMAT = ('{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}'
                  '{{else}}none{{end}}|{{.State.OOMKilled}}|{{.RestartCount}}')
BACKUP_FILES = ('database.sql.gz', 'spug-data.tar.gz')
BACKUP_REQUIRED = BACKUP_FILES + ('SHA256SUMS',)
BACKUP_NAME = re.compile(r'^\d{8}-\d{6}(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?$')
MANIFEST_LINE = re.compile(r'^([0-9a-fA-F]{64}) [ *](database\.sql\.gz|spug-data\.tar\.gz)$')
CommandResult = namedtuple('CommandResult', 'returncode stdout')


def run_command(args, timeout=4):
    """No shell, no stderr disclosure, bounded stdout and bounded command time."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, shell=False, bufsize=0)
    chunks = []
    oversized = threading.Event()

    def read_output():
        remaining = 16384
        try:
            while True:
                chunk = proc.stdout.read(min(4096, remaining + 1))
                if not chunk:
                    return
                if len(chunk) > remaining:
                    oversized.set()
                    proc.kill()
                    return
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError:
            oversized.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        reader.join(timeout=1)
        proc.stdout.close()
    if oversized.is_set() or reader.is_alive():
        raise ValueError('Command output unavailable')
    return CommandResult(proc.returncode, b''.join(chunks).decode('utf-8', errors='strict'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def http_status():
    # Ignore proxy environment variables and never follow a redirect off this host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request('http://127.0.0.1:8080/api/account/login/', method='GET')
    try:
        with opener.open(request, timeout=4) as response:
            return response.status  # Never read or emit the response body.
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        return code


def disk_usage(path):
    values = shutil.disk_usage(path)
    fs = os.statvfs(path)
    return values.total, values.used, values.free, fs.f_files, fs.f_favail


def check(check_id, name, status, summary, advice=''):
    return {'id': check_id, 'name': name, 'status': status, 'summary': summary, 'advice': advice}


def overall(checks):
    states = {item['status'] for item in checks}
    return next((state for state in ('error', 'warning', 'unknown') if state in states), 'ok')


def safe_directory(path):
    absolute = path.absolute()
    if path.resolve(strict=True) != absolute or not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError('Unsafe directory')
    return absolute


def open_regular(path, directory_fd=None):
    """Open only an exact regular file, without symlinks, relative to a pinned dir."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('Unsafe backup file')
    if directory_fd is not None:
        fd = os.open(path.name, flags, dir_fd=directory_fd)
    else:
        if path.resolve(strict=True) != path.absolute():
            raise ValueError('Unsafe backup path')
        fd = os.open(str(path), flags)
    after = os.fstat(fd)
    if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
        os.close(fd)
        raise ValueError('Backup file changed')
    return os.fdopen(fd, 'rb')


class HostDiagnostic:
    def __init__(self, root=ROOT, runner=run_command, http_get=http_status,
                 disk_probe=disk_usage, meminfo_path=Path('/proc/meminfo')):
        # Injection supports offline tests; the CLI exposes no arbitrary path/command options.
        self.root = Path(root)
        self.runner = runner
        self.http_get = http_get
        self.disk_probe = disk_probe
        self.meminfo_path = Path(meminfo_path)

    def command(self, args):
        try:
            result = self.runner(args, timeout=4)
            if (not isinstance(result.stdout, str) or len(result.stdout) > 16384
                    or result.returncode not in (0, 3)):
                return None
            return result
        except Exception:
            # Exception messages, stderr and command bodies must never enter a report.
            return None

    def container_state(self, name):
        result = self.command(['docker', 'inspect', '--format', INSPECT_FORMAT, name])
        if result is None or result.returncode != 0:
            return None
        parts = result.stdout.strip().split('|')
        if (len(parts) != 4 or parts[0] not in CONTAINER_STATES
                or parts[1] not in ('none', 'starting', 'healthy', 'unhealthy')
                or parts[2] not in ('true', 'false') or not re.fullmatch(r'\d{1,9}', parts[3])):
            return None
        return tuple(parts)

    def process_state(self, service):
        result = self.command(['docker', 'exec', 'spug', 'supervisorctl', 'status', service])
        if result is None:
            return None
        parts = result.stdout.strip().split()
        if len(parts) < 2 or parts[0] != service or parts[1] not in PROCESS_STATES:
            return None
        return parts[1]

    def container_check(self, name, state):
        check_id = 'host.container.' + name
        if state is None:
            return check(check_id, name + ' container', 'unknown', 'Container state is unavailable.',
                         'Use SSH to check Docker availability and the fixed container name.')
        status, health, oom, restarts = state
        if status != 'running':
            return check(check_id, name + ' container', 'error', 'Container is ' + status + '.',
                         'Diagnose locally. Only an exited or created container can be explicitly started.')
        if health == 'unhealthy' or oom == 'true':
            return check(check_id, name + ' container', 'error',
                         'Container is running with an unhealthy or OOM indication.',
                         'Inspect locally; this tool will not restart a running container.')
        if health == 'starting' or int(restarts) > 0:
            return check(check_id, name + ' container', 'warning',
                         'Container is running; health is starting or a restart was recorded.',
                         'Check the platform locally; restart history alone does not establish a current outage.')
        return check(check_id, name + ' container', 'ok', 'Container is running.',
                     'Process health does not prove business tasks succeeded.')

    def process_check(self, service, app_running):
        state = self.process_state(service) if app_running else None
        check_id = 'host.service.' + service
        if state is None or state == 'UNKNOWN':
            return check(check_id, service + ' process', 'unknown', 'Process state is unavailable.',
                         'Check the application container and Supervisor through SSH.')
        if state == 'RUNNING':
            return check(check_id, service + ' process', 'ok', 'Process is RUNNING.',
                         'A running process does not prove business tasks succeeded.')
        return check(check_id, service + ' process', 'error' if state in STARTABLE_PROCESSES else 'warning',
                     'Process state is ' + state + '.',
                     'Only a STOPPED, EXITED or FATAL process can be explicitly started; automatic retries are not interrupted.')

    def disk_check(self):
        try:
            total, used, free, inodes, inode_free = self.disk_probe(self.root)
            if total <= 0 or not 0 <= free <= total:
                raise ValueError('Invalid disk information')
            remaining = 100.0 * free / total
            inode_remaining = 100.0 * inode_free / inodes if inodes > 0 else None
            minimum = min(remaining, inode_remaining if inode_remaining is not None else remaining)
            status = 'error' if minimum < 5 else 'warning' if minimum < 15 else 'ok'
            summary = 'Deployment disk has %.1f%% free space.' % remaining
            if inode_remaining is not None:
                summary += ' Free inodes: %.1f%%.' % inode_remaining
            return check('host.disk', 'Deployment disk', status, summary,
                         'Investigate storage pressure locally; this tool never deletes files.')
        except Exception:
            return check('host.disk', 'Deployment disk', 'unknown', 'Disk information is unavailable.',
                         'Check the filesystem containing /opt/spug through SSH.')

    def memory_check(self):
        try:
            values = {}
            with self.meminfo_path.open('r', encoding='ascii') as handle:
                for _ in range(128):
                    line = handle.readline(256)
                    if not line:
                        break
                    match = re.fullmatch(r'(MemTotal|MemAvailable):\s+(\d{1,15}) kB\s*', line)
                    if match:
                        values[match[1]] = int(match[2])
            total, available = values['MemTotal'], values['MemAvailable']
            if total <= 0 or not 0 <= available <= total:
                raise ValueError('Invalid memory information')
            remaining = 100.0 * available / total
            return check('host.memory', 'Host memory',
                         'error' if remaining < 5 else 'warning' if remaining < 15 else 'ok',
                         'Available memory: %.1f%%.' % remaining,
                         'Inspect host memory pressure locally; no processes are killed by this tool.')
        except Exception:
            return check('host.memory', 'Host memory', 'unknown', 'Memory information is unavailable.',
                         'Check /proc/meminfo on the Linux deployment host.')

    def backup_check(self, verify=False):
        check_id, name = 'host.backup', 'Latest paired backup'
        advice = 'Recovery has not been rehearsed by this check. Keep database and data/key archives together.'
        fd = None
        try:
            backup_root = safe_directory(self.root / 'backups')
            candidates = []
            for entry in backup_root.iterdir():
                if BACKUP_NAME.fullmatch(entry.name):
                    # A symlink with a timestamp name is also an unsafe backup attempt.
                    if entry.is_symlink():
                        candidates.append(entry)
                    elif entry.is_dir() and any((entry / part).exists() or (entry / part).is_symlink()
                                                 for part in BACKUP_REQUIRED):
                        candidates.append(entry)
            if not candidates:
                return check(check_id, name, 'warning', 'No paired backup attempt was found.', advice)
            latest = max(candidates, key=lambda entry: entry.name)
            safe_directory(latest)
            if os.open in os.supports_dir_fd:
                fd = os.open(str(latest), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                             | getattr(os, 'O_NOFOLLOW', 0))
            for part in BACKUP_REQUIRED:
                try:
                    with open_regular(latest / part, fd) as handle:
                        if os.fstat(handle.fileno()).st_size <= 0:
                            raise ValueError('Empty backup file')
                except FileNotFoundError:
                    return check(check_id, name, 'warning', 'The latest paired backup attempt is incomplete.', advice)
            if not verify:
                return check(check_id, name, 'ok',
                             'Paired archives and checksum manifest are present; hashes are not verified.', advice)
            with open_regular(latest / 'SHA256SUMS', fd) as handle:
                raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError('Manifest too large')
            hashes = {}
            for line in raw.decode('ascii').splitlines():
                match = MANIFEST_LINE.fullmatch(line)
                if not match or match[2] in hashes:
                    raise ValueError('Unsafe manifest')
                hashes[match[2]] = match[1].lower()
            if set(hashes) != set(BACKUP_FILES):
                raise ValueError('Incomplete manifest')
            deadline = time.monotonic() + 120
            for part in BACKUP_FILES:
                digest = hashlib.sha256()
                with open_regular(latest / part, fd) as handle:
                    while True:
                        if time.monotonic() > deadline:
                            raise ValueError('Verification timed out')
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                if digest.hexdigest() != hashes[part]:
                    raise ValueError('Digest mismatch')
            return check(check_id, name, 'ok', 'Both paired archives passed SHA-256 verification.', advice)
        except FileNotFoundError:
            return check(check_id, name, 'warning', 'The backup directory or paired files are missing.', advice)
        except Exception:
            return check(check_id, name, 'error', 'The latest paired backup could not be safely checked or verified.',
                         'Inspect backup permissions, regular files and the fixed two-file checksum manifest. ' + advice)
        finally:
            if fd is not None:
                os.close(fd)

    def report(self, verify_backup=False):
        states = {name: self.container_state(name) for name in CONTAINERS}
        checks = [self.container_check(name, states[name]) for name in CONTAINERS]
        app_running = states['spug'] is not None and states['spug'][0] == 'running'
        checks.extend(self.process_check(service, app_running) for service in SERVICES)
        try:
            code = self.http_get()
            if not isinstance(code, int) or not 100 <= code <= 599:
                raise ValueError('Invalid HTTP status')
            checks.append(check('host.http', 'Local HTTP endpoint', 'ok' if code == 200 else 'error',
                                'Local HTTP endpoint returned status %d.' % code,
                                'This checks only the fixed local login endpoint; no response content is collected.'))
        except Exception:
            checks.append(check('host.http', 'Local HTTP endpoint', 'unknown', 'Local HTTP endpoint is unavailable.',
                                'Check the fixed loopback port and nginx through SSH.'))
        checks.extend((self.disk_check(), self.memory_check(), self.backup_check(verify_backup)))
        return {
            'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'status': overall(checks), 'checks': checks,
            'limitations': [
                'Running containers/processes and HTTP availability do not prove business tasks succeeded.',
                'Backup presence, SHA-256 integrity and a successful recovery rehearsal are distinct.',
                'Only this deployment host is checked; no customer server is contacted.',
            ],
        }

    def start(self, target):
        name = target if target in TARGETS else 'unsupported target'
        def result(status, summary, advice):
            return check('host.recovery', 'Manual start: ' + name, status, summary, advice)
        if target not in TARGETS:
            return result('warning', 'Start refused: unsupported target.', 'Choose exactly one fixed container or process.')
        lock = self.root / '.platform-health.lock'
        fd = None
        try:
            safe_directory(self.root)
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            if target in CONTAINERS:
                state = self.container_state(target)
                if state is None or state[0] not in ('created', 'exited'):
                    return result('warning', 'Start refused: container is running or not in an allowed stopped state.',
                                  'Diagnose the fresh state locally; no restart or recreate is performed.')
                args = ['docker', 'start', target]
            else:
                app = self.container_state('spug')
                if app is None or app[0] != 'running' or self.process_state(target) not in STARTABLE_PROCESSES:
                    return result('warning', 'Start refused: process is running, retrying or its state is unavailable.',
                                  'Only STOPPED, EXITED or FATAL can be started in a running application container.')
                args = ['docker', 'exec', 'spug', 'supervisorctl', 'start', target]
            outcome = self.command(args)
            if outcome is None or outcome.returncode != 0:
                return result('unknown', 'Start outcome is unavailable; the command was not retried.',
                              'Run a fresh diagnostic to verify state before taking further action.')
            if target in CONTAINERS:
                current = self.container_state(target)
                running = current is not None and current[0] == 'running'
            else:
                running = self.process_state(target) == 'RUNNING'
            return result('ok' if running else 'unknown',
                          'Start accepted; target is running.' if running else 'Start accepted; target has not yet been confirmed running.',
                          'Run a fresh diagnostic to verify platform availability and business task results.')
        except FileExistsError:
            return result('warning', 'Start refused: another start operation holds the lock.',
                          'Wait for the current SSH operation; inspect a stale lock manually before removing it.')
        except Exception:
            return result('unknown', 'Start could not be safely completed.', 'Use SSH to verify state; no automatic retry is performed.')
        finally:
            if fd is not None:
                os.close(fd)
                lock.unlink()

    def write_report(self, report):
        root = safe_directory(self.root)
        fd, temporary = tempfile.mkstemp(prefix='.platform-health-', suffix='.json', dir=str(root))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(report, handle, ensure_ascii=True, separators=(',', ':'))
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, str(root / 'platform-health.json'))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def publish_report(self):
        try:
            safe_directory(self.root)
            with open_regular(self.root / 'platform-health.json'):
                pass
            state = self.container_state('spug')
            if state is None or state[0] != 'running':
                return check('host.publish', 'Publish host report', 'warning',
                             'Report was not copied: the application container is not confirmed running.',
                             'The host JSON remains available through SSH; no container is started automatically.')
            result = self.command(['docker', 'cp', str(self.root / 'platform-health.json'),
                                   'spug:/data/platform-health.json'])
            if result is not None and result.returncode == 0:
                return check('host.publish', 'Publish host report', 'ok',
                             'The safe host JSON was copied to the fixed application report path.')
        except Exception:
            pass
        return check('host.publish', 'Publish host report', 'unknown',
                     'Report copy could not be confirmed.',
                     'Use SSH to check the host JSON and fixed destination; no automatic retry is performed.')


def main(argv=None, diagnostic=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-backup', action='store_true', help='Stream SHA-256 checks for the latest fixed paired backup (120s budget).')
    parser.add_argument('--write-report', action='store_true', help='Atomically write /opt/spug/platform-health.json (private permissions).')
    parser.add_argument('--publish-report', action='store_true', help='With --write-report, copy safe JSON to running spug:/data/platform-health.json.')
    parser.add_argument('--start', choices=TARGETS, help='Explicitly start one stopped container or fixed failed/stopped process.')
    args = parser.parse_args(argv)
    if args.publish_report and not args.write_report:
        parser.error('--publish-report requires --write-report')
    diagnostic = diagnostic or HostDiagnostic()
    recovery = None
    if args.start:
        try:
            recovery = diagnostic.start(args.start)
        except Exception:
            recovery = check('host.recovery', 'Manual start', 'unknown',
                             'Start could not be safely completed.',
                             'Use SSH to verify state; the operation was not retried.')
    try:
        report = diagnostic.report(verify_backup=args.verify_backup)
    except Exception:
        report = {
            'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'status': 'unknown',
            'checks': [check('host.diagnostic', 'Host diagnostic', 'unknown',
                             'The diagnostic snapshot is unavailable.', 'Check the deployment locally through SSH.')],
            'limitations': ['A failed diagnostic is not evidence that the platform is healthy.'],
        }
    if recovery:
        report['checks'].append(recovery)
        report['status'] = overall(report['checks'])
    if args.write_report:
        try:
            diagnostic.write_report(report)
        except Exception:
            report['checks'].append(check('host.report', 'Host report write', 'error',
                                          'The host report could not be safely written.',
                                          'Check /opt/spug permissions locally; standard output still contains the safe snapshot.'))
            report['status'] = overall(report['checks'])
        else:
            if args.publish_report:
                report['checks'].append(diagnostic.publish_report())
                report['status'] = overall(report['checks'])
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['status'] == 'ok' else 2 if report['status'] == 'error' else 1


if __name__ == '__main__':
    raise SystemExit(main())
