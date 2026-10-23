# Common code is embedded into each generated Spug template; no pip dependencies.
import sys

if sys.version_info < (3, 8):
    sys.stderr.write('ERROR: requires Python 3.8+\n')
    sys.exit(2)

import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import time
import unicodedata

SAFE_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'
BRANCHES = ('54', '55', '56', '70', '71', '72', '73', '74', '80', '81', '82', '83', '84', '85')
# Stable policy across Python 3.8 patch levels; do not rely solely on is_global.
BLOCKED_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
    '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
    '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24',
    '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4',
    '2001::/23', '2001:db8::/32', '2002::/16', '3fff::/20',
))
PUBLIC_V6 = ipaddress.ip_network('2000::/3')


class CheckError(Exception):
    pass


def clean(value, limit=12000):
    text = str(value)
    text = ''.join(c for c in text[:limit]
                   if c in '\n\t' or not unicodedata.category(c).startswith('C'))
    return text + ('\n[output truncated]' if len(str(value)) > limit else '')


def emit(value):
    print(clean(value))


def read_text(path, limit=262144):
    """Open an absolute regular file, never follow links, bound read size.

    Linux uses anchored directory fds, including parent components. The Windows
    branch exists for offline fixture tests only; template entrypoints require Linux.
    """
    path = os.path.abspath(path)
    fd = None
    parent = None
    try:
        if os.name == 'posix':
            parts = path.split('/')[1:]
            parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent)
                os.close(parent)
                parent = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
        else:
            for part in (path, *list(Path(path).parents)):
                if stat.S_ISLNK(os.lstat(part).st_mode):
                    raise CheckError('symbolic link refused')
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise CheckError('not a regular file')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CheckError('not a regular file')
        chunks = []
        count = 0
        while count <= limit:
            chunk = os.read(fd, min(8192, limit + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        if count > limit:
            raise CheckError('file exceeds inspection size limit')
        return b''.join(chunks).decode('utf-8', errors='replace')
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def run(args, timeout=6, limit=48000, truncate=False):
    """No shell, clean environment, bounded pipe, terminate whole child group."""
    exe = args[0] if args[0].startswith('/') else shutil.which(args[0], path=SAFE_PATH)
    if not exe or not os.path.isfile(exe):
        raise CheckError('required command unavailable: ' + args[0])
    env = {'PATH': SAFE_PATH, 'LANG': 'C', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat',
           'SYSTEMD_COLORS': '0'}
    proc = subprocess.Popen([exe] + args[1:], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)
    data = bytearray()
    cutoff = time.monotonic() + timeout
    clipped = False
    try:
        while True:
            remaining = cutoff - time.monotonic()
            if remaining <= 0:
                raise CheckError('command timed out: ' + args[0])
            ready, _, _ = select.select([proc.stdout], [], [], min(0.2, remaining))
            if not ready:
                continue
            chunk = os.read(proc.stdout.fileno(), min(4096, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                if not truncate:
                    raise CheckError('command output exceeds limit: ' + args[0])
                clipped = True
                break
        if not clipped:
            proc.wait(timeout=max(0.01, cutoff - time.monotonic()))
            if proc.returncode:
                # Do not echo command stderr: it can contain configuration secrets.
                raise CheckError('command failed (%s): %s' % (proc.returncode, args[0]))
        return clean(data[:limit].decode('utf-8', errors='replace'), limit) + (
            '\n[output truncated]' if clipped else '')
    except subprocess.TimeoutExpired:
        raise CheckError('command timed out: ' + args[0])
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)
        proc.stdout.close()


def php_branches():
    value = os.environ.get('_SPUG_PHP_VERSION', 'all') or 'all'
    if value == 'all':
        return list(BRANCHES)
    branch = value.replace('.', '')
    if branch not in BRANCHES or value not in (branch, branch[0] + '.' + branch[1]):
        raise CheckError('PHP_VERSION must be all or a supported branch, e.g. 7.4')
    return [branch]


def installed_branches():
    found = []
    for branch in php_branches():
        path = '/www/server/php/' + branch
        if os.path.isdir(path) and os.path.realpath(path) == path:
            found.append(branch)
    if not found:
        raise CheckError('no selected Baota PHP directory found under /www/server/php')
    return found


def domain_name(value):
    if not isinstance(value, str) or len(value) > 253 or value != value.strip():
        raise CheckError('DOMAIN must be a public hostname only')
    try:
        domain = value.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise CheckError('invalid DOMAIN')
    labels = domain.split('.')
    if len(labels) < 2 or len(domain) > 253 or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in labels):
        raise CheckError('DOMAIN must omit protocol, port, path and credentials')
    if labels[-1] in ('local', 'localhost', 'internal', 'invalid', 'test', 'example', 'onion'):
        raise CheckError('public DOMAIN required')
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise CheckError('IP literals are not supported')


def resolve_dns(domain):
    # libc/NSS DNS can ignore a Python signal until its call returns. Isolate it
    # in a killable worker, with a clean environment and no shell interpolation.
    code = ('import json,socket,sys; print(json.dumps(socket.getaddrinfo('
            'sys.argv[1],443,type=socket.SOCK_STREAM)))')
    return json.loads(run([sys.executable, '-I', '-c', code, domain], timeout=8, limit=16384))


def public_addresses(domain):
    answers = resolve_dns(domain)
    if not answers or len(answers) > 32:
        raise CheckError('DNS returned no address or too many addresses')
    result = []
    for family, kind, proto, _, address in answers:
        ip = ipaddress.ip_address(address[0])
        if (family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global
                or ip.is_multicast or ip.is_unspecified or ip.is_reserved
                or getattr(ip, 'ipv4_mapped', None) is not None
                or (ip.version == 6 and ip not in PUBLIC_V6)
                or any(ip.version == network.version and ip in network
                       for network in BLOCKED_NETWORKS)):
            raise CheckError('DNS contains a non-public address; request refused')
        pair = (family, tuple(address))
        if pair not in result:
            result.append(pair)
    return result


def connect_tls(domain):
    # Resolve once, validate ALL answers, connect numerically to prevent rebinding.
    family, address = public_addresses(domain)[0]
    context = ssl.create_default_context()  # chain + hostname verification enabled
    raw = socket.socket(family, socket.SOCK_STREAM)
    try:
        raw.settimeout(8)
        raw.connect(address)
        return context.wrap_socket(raw, server_hostname=domain)
    except BaseException:
        raw.close()
        raise


def entrypoint(main):
    if not sys.platform.startswith('linux'):
        emit('ERROR: this template requires Linux and Python 3.8+')
        return 2

    def expired(*_):
        raise CheckError('inspection exceeded 60-second total limit')

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(60)
    try:
        result = main()
        return result or 0
    except CheckError as error:
        emit('ERROR: ' + str(error))
    except ssl.SSLCertVerificationError:
        emit('ERROR: TLS certificate chain/hostname verification failed')
    except (socket.timeout, TimeoutError):
        emit('ERROR: network timeout')
    except socket.gaierror:
        emit('ERROR: DNS lookup failed')
    except (OSError, ssl.SSLError, ValueError):
        emit('ERROR: check unavailable (permissions, dependency, file or connection); no changes made')
    finally:
        signal.alarm(0)
    return 2
