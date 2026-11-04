"""Disposable local process: saved credentials in stdin, public JSON in stdout."""
import json
import logging
import os
import sys
from types import SimpleNamespace

# The shared Baota certificate parser belongs to a registered operations package
# whose imports include models. Initialize the registry only; no database call is
# made. The caller's explicit isolated test setting is inherited unchanged.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
import django
django.setup()

from .collectors import (CollectorError, MAX_OUTPUT_BYTES, _discover_panel, _host_result,
                         _host_transport, _normalize_host)


def main():
    logging.disable(logging.CRITICAL)
    try:
        wire = sys.stdin.buffer.read(MAX_OUTPUT_BYTES + 1)
        if len(wire) > MAX_OUTPUT_BYTES:
            raise CollectorError('output_limit')
        payload = json.loads(wire)
        if payload.get('action') == 'tls':
            from .tls import _check_tls
            result = _check_tls(payload['domain'], payload['port'])
        elif payload.get('kind') == 'panel' and payload.get('action') == 'discover':
            panel = SimpleNamespace(api_base_url=payload['api_base_url'], tls_ca=payload['tls_ca'],
                                    tls_fingerprint=payload['tls_fingerprint'], get_api_key=lambda: payload['api_key'])
            result = _discover_panel(panel)
        elif payload.get('kind') == 'host':
            from libs.ssh import SSH
            host = SimpleNamespace(get_ssh=lambda: SSH(payload['hostname'], payload['port'],
                                                       payload['username'], payload['private_key']))
            raw = _host_transport(host, payload['action'], payload.get('plan'))
            result = _normalize_host(raw) if payload['action'] == 'discover' else _host_result(raw, payload.get('plan'))
        else:
            raise CollectorError('unsupported')
        output = json.dumps({'result': result}, allow_nan=False, separators=(',', ':'))
        if len(output.encode('utf8')) > MAX_OUTPUT_BYTES:
            raise CollectorError('output_limit')
    except CollectorError as error:
        output = json.dumps({'error': error.code})
    except Exception:
        output = '{"error":"collection_failed"}'
    sys.stdout.write(output)


if __name__ == '__main__':
    main()
