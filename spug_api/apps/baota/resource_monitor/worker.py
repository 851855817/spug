"""Private subprocess entry point: credentials in stdin, normalized metrics in stdout."""
import json
import logging
import sys
from types import SimpleNamespace

from .collector import CollectorError, _collect_host, _collect_panel


def main():
    logging.disable(logging.CRITICAL)
    try:
        # Payload contains only locally saved connection credentials. Bound it before JSON parsing.
        wire = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(wire) > 256 * 1024:
            raise CollectorError('collection_failed')
        payload = json.loads(wire)
        if payload['kind'] == 'panel':
            panel = SimpleNamespace(api_base_url=payload['api_base_url'], tls_ca=payload['tls_ca'],
                                    tls_fingerprint=payload['tls_fingerprint'],
                                    get_api_key=lambda: payload['api_key'])
            metrics = _collect_panel(panel)
        elif payload['kind'] == 'host':
            from libs.ssh import SSH
            host = SimpleNamespace(get_ssh=lambda: SSH(payload['hostname'], payload['port'],
                                                       payload['username'], payload['private_key']))
            metrics = _collect_host(host)
        else:
            raise CollectorError('collection_failed')
        result = {'metrics': metrics}
    except CollectorError as error:
        result = {'error': error.code}
    except Exception:
        result = {'error': 'collection_failed'}
    sys.stdout.write(json.dumps(result, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
