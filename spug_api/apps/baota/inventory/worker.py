"""Private, database-free reader subprocess: input credentials, output safe rows."""
import json
import logging
import sys
from types import SimpleNamespace

from apps.baota.client import PanelClient
from .readers import InventoryError, MAX_OUTPUT_BYTES, read_inventory


def main():
    logging.disable(logging.CRITICAL)
    try:
        wire = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(wire) > 256 * 1024:
            raise InventoryError('invalid_response')
        payload = json.loads(wire)
        panel = SimpleNamespace(api_base_url=payload['api_base_url'], tls_ca=payload['tls_ca'],
                                tls_fingerprint=payload['tls_fingerprint'],
                                get_api_key=lambda: payload['api_key'])
        with PanelClient(panel) as client:
            result = {'rows': read_inventory(client, payload['kind'])}
        output = json.dumps(result, allow_nan=False, separators=(',', ':'))
        if len(output.encode('utf-8')) > MAX_OUTPUT_BYTES:
            raise InventoryError('limit')
    except InventoryError as error:
        output = json.dumps({'error': error.code})
    except Exception:
        output = json.dumps({'error': 'collection_failed'})
    sys.stdout.write(output)


if __name__ == '__main__':
    main()
