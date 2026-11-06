"""Exercise the local child authorization protocol without any network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest import TestCase


class WorkerAuthorizationTests(TestCase):
    def helper(self):
        self.assertIsNotNone(importlib.util.find_spec('libs.worker_authorization'), 'child authorization protocol missing')
        from libs.worker_authorization import exchange
        return exchange

    def child(self, source):
        return subprocess.Popen([sys.executable, '-B', '-c', source], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def test_each_child_request_must_be_authorized_by_the_parent(self):
        exchange = self.helper()
        source = """import json,sys
payload=json.loads(sys.stdin.buffer.readline())
for i in range(2):
 print(json.dumps({'request':'authorize'}),flush=True)
 assert json.loads(sys.stdin.buffer.readline()) == {'authorized':True}
print(json.dumps({'result':payload['value']}),flush=True)
"""
        calls = []
        output = exchange(self.child(source), {'value': 7}, lambda: calls.append(True), 5, 1024)
        self.assertEqual(json.loads(output), {'result': 7})
        self.assertEqual(len(calls), 4)  # admission, two remote boundaries, result

    def test_revocation_kills_child_before_the_next_operation(self):
        exchange = self.helper()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'should-not-exist'
            source = """import json,sys,pathlib
payload=json.loads(sys.stdin.buffer.readline())
for i in range(2):
 print(json.dumps({'request':'authorize'}),flush=True)
 assert json.loads(sys.stdin.buffer.readline()) == {'authorized':True}
pathlib.Path(payload['marker']).write_text('incorrect')
print('{}',flush=True)
"""
            calls = []
            def authorize():
                calls.append(True)
                if len(calls) == 3:
                    raise PermissionError('revoked')
            with self.assertRaises(PermissionError):
                exchange(self.child(source), {'marker': str(marker)}, authorize, 5, 1024)
            self.assertFalse(marker.exists())

    def test_oversized_child_output_is_rejected_and_reaped(self):
        exchange = self.helper()
        source = "import sys;sys.stdin.buffer.readline();print('x'*2048,flush=True)"
        child = self.child(source)
        with self.assertRaises(ValueError):
            exchange(child, {}, lambda: None, 5, 100)
        self.assertIsNotNone(child.poll())

    def test_initial_input_write_is_covered_by_the_deadline(self):
        child = self.child('import time;time.sleep(2)')
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.helper()(child, {'fixture': 'x' * (128 * 1024)}, lambda: None, .1, 1024)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNotNone(child.poll())

    def test_authorization_reply_backpressure_is_covered_by_the_deadline(self):
        child = self.child('import sys,time;sys.stdin.buffer.readline();'
                           'sys.stdout.write(\'{"request":"authorize"}\\n\'*1000);'
                           'sys.stdout.flush();time.sleep(2)')
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.helper()(child, {}, lambda: None, .15, 1024)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNotNone(child.poll())

    def test_inventory_worker_protocol_checks_each_request_without_network(self):
        from unittest.mock import patch
        from apps.baota.inventory import readers
        from libs.worker_authorization import guard
        actual_popen = subprocess.Popen
        bootstrap = '''
import apps.baota.inventory.worker as worker
from libs.worker_authorization import authorize_remote
class Fixture:
 def __init__(self,panel): pass
 def __enter__(self): return self
 def __exit__(self,*args): pass
 def request(self,path,action,params=None,**kwargs):
  authorize_remote()
  if action=='GetSystemTotal': return {'version':'13.1.0','system':'Ubuntu Linux'}
  return {'data':[]}
worker.PanelClient=Fixture
worker.main()
'''
        calls = []
        def launch(*args, **kwargs):
            return actual_popen([sys.executable, '-B', '-c', bootstrap], **kwargs)
        payload = {'kind': 'sites', 'api_base_url': '', 'api_key': '', 'tls_ca': '', 'tls_fingerprint': ''}
        with patch.object(readers.subprocess, 'Popen', side_effect=launch), guard(lambda: calls.append(True)):
            self.assertEqual(readers.run_worker(payload), [])
        self.assertGreaterEqual(len(calls), 5)
