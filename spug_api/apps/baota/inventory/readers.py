"""Fixed, bounded panel readers. Raw configuration never leaves this module.

getData is a metadata query, but the panel may initialize caches, persist its
website page size/sort preferences, and remove obsolete backup .pl markers.
No download, hashing, backup, restoration, or cloud-provider request is made.
"""
import json
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import time
from datetime import datetime

from apps.baota.client import PanelAPIError
from apps.baota.resources import system_info, php_branch, site_ssl_summary
from apps.baota.validation import positive_id


TOTAL_TIMEOUT = 45
MAX_REQUESTS = 200
MAX_ASSETS = 1000
MAX_DOMAINS = 5000
PAGE_SIZE = 100
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MESSAGES = {'collection_failed': '采集失败，请检查面板 API 连接和配置',
            'invalid_response': '面板资产格式不受支持，已保留上次快照',
            'limit': '资产数量或请求次数超出单次限制，已保留上次快照',
            'timeout': '采集超过 45 秒时限，已保留上次快照',
            'unsupported': '面板版本尚未适配，请先测试连接并核对版本'}
DOMAIN = re.compile(r'(?:\*\.)?[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?\Z')
ASSET_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}\Z')
SAFE_BASENAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}\Z')


class InventoryError(Exception):
    def __init__(self, code):
        self.code = code if code in MESSAGES else 'collection_failed'
        super().__init__(MESSAGES[self.code])


class BudgetClient:
    def __init__(self, client):
        self.client, self.deadline, self.calls = client, time.monotonic() + TOTAL_TIMEOUT, 0

    def request(self, path, action, params=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise InventoryError('timeout')
        if self.calls >= MAX_REQUESTS:
            raise InventoryError('limit')
        self.calls += 1
        # PanelClient bounds connect + response duration, including trickled data.
        timeout = min(remaining, 12)
        return self.client.request(path, action, params, timeout=(min(3, timeout / 2), timeout / 2))


def _id(value):
    try:
        return positive_id(value)
    except ValueError:
        raise InventoryError('invalid_response') from None


def _name(value):
    if not isinstance(value, str) or not ASSET_NAME.fullmatch(value):
        raise InventoryError('invalid_response')
    return value


def _table(client, name, maximum):
    rows, seen = [], set()
    for page in range(1, maximum // PAGE_SIZE + 2):
        response = client.request('/data', 'getData', {'table': name, 'p': page, 'limit': PAGE_SIZE})
        data = response.get('data') if isinstance(response, dict) else None
        if not isinstance(data, list) or len(data) > PAGE_SIZE:
            raise InventoryError('invalid_response')
        for row in data:
            if not isinstance(row, dict):
                raise InventoryError('invalid_response')
            rid = _id(row.get('id'))
            if rid in seen:
                raise InventoryError('invalid_response')
            seen.add(rid)
            rows.append(row)
        if len(rows) > maximum:
            raise InventoryError('limit')
        if len(data) < PAGE_SIZE:
            return rows
    raise InventoryError('limit')


def _sites(client):
    result = []
    for raw in _table(client, 'sites', MAX_ASSETS):
        summary = site_ssl_summary(raw.get('ssl'))
        status = raw.get('status')
        result.append({'site_id': _id(raw['id']), 'name': _name(raw.get('name')), 'domains': [],
                       'php_version': php_branch(raw.get('php_version')),
                       'site_status': {'0': 'stopped', '1': 'running'}.get(str(status), 'unknown')
                       if type(status) in (int, str) else 'unknown',
                       'ssl_state': summary['state'], 'expires_at': summary['expires_at']})
    return result


def _domains(client, sites):
    by_id = {site['site_id']: site for site in sites}
    for raw in _table(client, 'domain', MAX_DOMAINS):
        pid, name, port = _id(raw.get('pid')), raw.get('name'), raw.get('port')
        if (not isinstance(name, str) or not DOMAIN.fullmatch(name) or '..' in name
                or isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535):
            raise InventoryError('invalid_response')
        # Orphaned rows are not attached to another site's identity.
        if pid in by_id:
            domain = {'name': name.lower(), 'port': int(port)}
            if domain not in by_id[pid]['domains']:
                by_id[pid]['domains'].append(domain)
    for site in sites:
        site['domains'].sort(key=lambda row: (row['name'], row['port']))


def _flag(value):
    if type(value) in (int, str) and str(value) in ('0', '1'):
        return str(value) == '1'
    return value if type(value) is bool else None


def _size(value):
    if type(value) is int and 0 <= value < 2 ** 63:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,19}', value) and int(value) < 2 ** 63:
        return int(value)
    return None


def _backup_time(value):
    # Panel addtime has no offset. Preserve its wall time; the API and UI disclose
    # that age assumes the platform timezone, rather than a verified panel clock.
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').isoformat()
    except ValueError:
        return None


def _backup(client, asset_type, asset_id, name):
    # type is excluded from the official response fields. Query each asset with
    # both pid/search and type, otherwise site/database id collisions are unsafe.
    response = client.request('/data', 'getData', {'table': 'backup', 'search': asset_id,
                              'type': '0' if asset_type == 'site' else '1',
                              'p': 1, 'limit': 1, 'order': 'addtime desc'})
    data = response.get('data') if isinstance(response, dict) else None
    if not isinstance(data, list) or len(data) > 1:
        raise InventoryError('invalid_response')
    result = {'asset_type': asset_type, 'asset_id': asset_id, 'name': name, 'backup_id': None,
              'backup_name': '', 'backup_at': None, 'size_bytes': None, 'local_exists': None,
              'in_progress': None, 'location': 'unknown', 'state': 'no_record'}
    if not data:
        return result
    raw = data[0]
    if not isinstance(raw, dict) or _id(raw.get('pid')) != asset_id:
        raise InventoryError('invalid_response')
    local, progress, size = _flag(raw.get('localexist')), _flag(raw.get('backup_status')), _size(raw.get('size'))
    filename = raw.get('filename')
    basename, location = '', 'unknown'
    if isinstance(filename, str) and len(filename) <= 4096:
        first = filename.split('|', 1)[0]
        location = 'cloud_record' if '|' in filename or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', first) else (
            'local' if first.startswith('/') else 'unknown')
        candidate = posixpath.basename(first) if location == 'local' or first.startswith('/') else ''
        if SAFE_BASENAME.fullmatch(candidate) and candidate not in ('.', '..'):
            basename = candidate
    state = ('in_progress' if progress is True else
             'unknown' if progress is None else
             'local_missing' if local is False else
             'unknown' if local is None or size is None else
             'empty' if size == 0 else 'panel_reported')
    result.update(backup_id=_id(raw.get('id')), backup_name=basename,
                  backup_at=_backup_time(raw.get('addtime')), size_bytes=size,
                  local_exists=local, in_progress=progress, location=location, state=state)
    return result


def read_inventory(client, kind):
    client = BudgetClient(client)
    try:
        system_info(client)  # Fixed reviewed Linux versions, no extra credential/config reads.
        sites = _sites(client)
        if kind == 'sites':
            _domains(client, sites)
            return sites
        if kind != 'backups':
            raise InventoryError('invalid_response')
        databases = _table(client, 'databases', MAX_ASSETS)
        if len(sites) + len(databases) > MAX_ASSETS:
            raise InventoryError('limit')
        assets = [('site', site['site_id'], site['name']) for site in sites]
        assets += [('database', _id(raw['id']), _name(raw.get('name'))) for raw in databases]
        return [_backup(client, *asset) for asset in assets]
    except InventoryError:
        raise
    except (PanelAPIError, ValueError) as error:
        raise InventoryError('timeout' if '超时' in str(error) else 'invalid_response') from None


def run_worker(payload):
    """A killable process includes OS DNS in the total limit; it cannot write DB.

    Secrets are sent via stdin only, never argv, environment, files or logs.
    """
    child, finished, started = None, False, time.monotonic()
    try:
        wire = json.dumps(payload, allow_nan=False).encode('utf-8')
        child = subprocess.Popen([sys.executable, '-m', 'apps.baota.inventory.worker'],
                                 cwd=str(Path(__file__).resolve().parents[3]),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        remaining = TOTAL_TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired('inventory worker', TOTAL_TIMEOUT)
        output, _ = child.communicate(wire, timeout=remaining)
        finished = True
        if child.returncode != 0 or len(output) > MAX_OUTPUT_BYTES:
            raise InventoryError('invalid_response')
        result = json.loads(output)
        if not isinstance(result, dict):
            raise InventoryError('invalid_response')
        if 'error' in result:
            raise InventoryError(result['error'])
        rows = result.get('rows')
        if not isinstance(rows, list) or len(rows) > MAX_ASSETS or any(not isinstance(r, dict) for r in rows):
            raise InventoryError('invalid_response')
        return rows
    except subprocess.TimeoutExpired:
        raise InventoryError('timeout') from None
    except InventoryError:
        raise
    except Exception:
        raise InventoryError('collection_failed') from None
    finally:
        if child is not None:
            if not finished:
                child.kill()
                child.wait()
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    stream.close()


def collect_panel(panel, kind):
    try:
        return run_worker({'kind': kind, 'api_base_url': panel.api_base_url, 'api_key': panel.get_api_key(),
                           'tls_ca': panel.tls_ca, 'tls_fingerprint': panel.tls_fingerprint})
    except InventoryError:
        raise
    except Exception:
        raise InventoryError('collection_failed') from None
