import importlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
import paramiko


def panel_fixture(version='9.2.0'):
    # Whitelisted fields follow system.py in the three pinned official releases.
    value = {
        'version': version, 'cpu': [12.5, 4, [1, 2, 3, 4], 'fixture cpu', 2, 1],
        'mem': {'memTotal': 8192, 'memRealUsed': 2048, 'memAvailable': 6144},
        'load': {'one': 0.5, 'five': 1.25, 'fifteen': 2.5, 'max': 8},
        'up': 1.5, 'down': 2.25,
        'disk': [{'path': '/', 'size': ['40G', '10G', '28G', '25%']}],
        'unexpected': {'password': 'must never escape'},
    }
    if version != '9.2.0':
        value['mem'].update(memNewTotal='8 GB', memNewRealUsed='2 GB')
        value['disk'][0]['size'].append('2 GB')
    return value


PROBE_FIXTURE = '''SPUG_RESOURCE_V1
sample
cpu\t1000\t600\t4
network\t1024\t2048\t100.00
sample
cpu\t1100\t650\t4
network\t3072\t6144\t101.00
memory\t8192\t6144
load\t0.5\t1.25\t2.5
disk\t41943040\t10485760\t25%\t/
SPUG_RESOURCE_END
'''.encode()


class LocalSSH:
    """Loopback-only transport fixture; command execution returns fixed counters."""

    def __init__(self, authenticate=True):
        owner = self
        self.command = None
        self.error = None
        self.event = threading.Event()
        self.key = paramiko.RSAKey.generate(1024)
        private = StringIO()
        self.key.write_private_key(private)
        self.private_key = private.getvalue()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(5)
        self.port = self.listener.getsockname()[1]
        self.transport = None

        class Server(paramiko.ServerInterface):
            def get_allowed_auths(self, username):
                return 'publickey'

            def check_auth_publickey(self, username, key):
                return paramiko.AUTH_SUCCESSFUL if authenticate and key == owner.key else paramiko.AUTH_FAILED

            def check_channel_request(self, kind, channel_id):
                return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_exec_request(self, channel, command):
                owner.command = command.decode()
                owner.event.set()
                return True

        def serve():
            try:
                connection, _ = self.listener.accept()
                self.transport = paramiko.Transport(connection)
                self.transport.add_server_key(self.key)
                self.transport.start_server(server=Server())
                channel = self.transport.accept(5)
                if channel is not None and self.event.wait(5):
                    channel.sendall(PROBE_FIXTURE)
                    channel.send_exit_status(0)
                    channel.close()
            except (OSError, EOFError, paramiko.SSHException) as error:
                self.error = type(error).__name__

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def close(self):
        self.listener.close()
        if self.transport is not None:
            self.transport.close()
        self.thread.join(timeout=2)


class CollectorTests(SimpleTestCase):
    def setUp(self):
        try:
            self.collector = importlib.import_module('apps.baota.resource_monitor.collector')
        except ModuleNotFoundError:
            self.fail('resource monitor collector has not been implemented')

    def collect_panel(self, raw):
        with patch.object(self.collector, 'PanelClient') as client:
            client.return_value.__enter__.return_value.request.return_value = raw
            metrics = self.collector._collect_panel(SimpleNamespace(
                api_base_url='https://fixture.invalid', api_key_ciphertext='fixture'))
        client.return_value.__enter__.return_value.request.assert_called_once_with(
            '/system', 'GetNetWork', timeout=(3, 8))
        return metrics

    def test_official_versions_use_binary_units_and_only_metric_fields(self):
        for version in ('9.2.0', '11.7.0', '13.1.0'):
            with self.subTest(version=version):
                result = self.collect_panel(panel_fixture(version))
                self.assertEqual(result, {
                    'cpu': {'percent': 12.5, 'cores': 4},
                    'memory': {'used_bytes': 2048 * 1024 ** 2,
                               'total_bytes': 8192 * 1024 ** 2, 'percent': 25.0},
                    'load': [0.5, 1.25, 2.5],
                    'network': {'up_bytes_sec': 1536.0, 'down_bytes_sec': 2304.0},
                    'disks': [{'path': '/', 'used_bytes': 10 * 1024 ** 3,
                               'total_bytes': 40 * 1024 ** 3, 'percent': 25.0}],
                })

    def test_invalid_numeric_fields_are_unknown_and_not_zero(self):
        for bad in (True, False, 'NaN', float('nan'), float('inf'), -1, 1e100, {}, []):
            with self.subTest(bad=repr(bad)):
                raw = panel_fixture()
                raw['cpu'][0] = bad
                raw['mem']['memRealUsed'] = bad
                raw['up'] = bad
                raw['load']['one'] = bad
                result = self.collect_panel(raw)
                self.assertIsNone(result['cpu']['percent'])
                self.assertIsNone(result['memory']['used_bytes'])
                self.assertIsNone(result['memory']['percent'])
                self.assertIsNone(result['network']['up_bytes_sec'])
                self.assertIsNone(result['load'][0])

    def test_out_of_range_percent_cores_and_inconsistent_memory_are_unknown(self):
        raw = panel_fixture()
        raw['cpu'] = [101, 2.5]
        raw['mem']['memRealUsed'] = 9000
        raw['disk'][0]['size'][3] = '101%'
        result = self.collect_panel(raw)
        self.assertEqual(result['cpu'], {'percent': None, 'cores': None})
        self.assertEqual(result['memory'], {'used_bytes': None,
                         'total_bytes': 8192 * 1024 ** 2, 'percent': None})
        self.assertIsNone(result['disks'][0]['percent'])

    def test_disk_units_missing_fields_unmounted_entries_and_limit(self):
        raw = panel_fixture()
        raw['disk'] = [
            {'path': '/data space', 'size': ['1.5 TB', '512 MiB', '0', '3.25 %']},
            {'path': '/unknown', 'size': ['NaN G', True]},
            {'path': 'None', 'size': '200G'},
            {'path': '/secret\npath', 'size': ['1G', '1M', '0', '1%']},
        ] + [{'path': '/mnt/%s' % i, 'size': ['8K', '4K', '4K', '50%']} for i in range(40)]
        result = self.collect_panel(raw)
        self.assertEqual(len(result['disks']), 32)
        self.assertEqual(result['disks'][0], {'path': '/data space',
                         'total_bytes': 1.5 * 1024 ** 4, 'used_bytes': 512 * 1024 ** 2,
                         'percent': 3.25})
        self.assertIsNone(result['disks'][1]['used_bytes'])
        self.assertIsNone(result['disks'][1]['total_bytes'])
        self.assertTrue(all(item['path'].startswith('/') for item in result['disks']))

    def test_binary_unit_scaling_does_not_round_past_signed_byte_limit(self):
        raw = panel_fixture()
        raw['disk'][0]['size'] = ['8 EiB', '8 EB', '0', '25%']
        result = self.collect_panel(raw)
        self.assertIsNone(result['disks'][0]['total_bytes'])
        self.assertIsNone(result['disks'][0]['used_bytes'])

    def test_unsupported_response_and_remote_errors_never_escape(self):
        for raw in (None, [], {'status': False, 'msg': 'secret response'}, {'unknown': 'secret'}):
            with self.subTest(raw=raw), self.assertRaises(self.collector.CollectorError) as raised:
                self.collect_panel(raw)
            self.assertNotIn('secret', str(raised.exception))
        with patch.object(self.collector, 'PanelClient', side_effect=RuntimeError('secret token')):
            with self.assertRaises(self.collector.CollectorError) as raised:
                self.collector._collect_panel(SimpleNamespace(api_base_url='https://fixture.invalid',
                                                            api_key_ciphertext='fixture'))
        self.assertNotIn('secret', str(raised.exception))

    def test_unconfigured_panel_does_not_open_client(self):
        with patch.object(self.collector, 'PanelClient') as client:
            with self.assertRaises(self.collector.CollectorError) as raised:
                self.collector.collect_panel(SimpleNamespace(api_base_url='', api_key_ciphertext=''))
        self.assertEqual(raised.exception.code, 'panel_unconfigured')
        client.assert_not_called()

    def test_probe_two_samples_compute_cpu_and_network(self):
        result = self.collector.parse_probe(PROBE_FIXTURE)
        self.assertEqual(result['cpu'], {'percent': 50.0, 'cores': 4})
        self.assertEqual(result['network'], {'up_bytes_sec': 2048.0, 'down_bytes_sec': 4096.0})
        self.assertEqual(result['memory'], {'total_bytes': 8192 * 1024,
                                           'used_bytes': 2048 * 1024, 'percent': 25.0})
        self.assertEqual(result['disks'][0]['total_bytes'], 40 * 1024 ** 3)

    def test_counter_reset_and_zero_duration_are_unknown(self):
        raw = PROBE_FIXTURE.replace(b'1100\t650', b'900\t550').replace(
            b'3072\t6144\t101.00', b'0\t0\t100.00')
        result = self.collector.parse_probe(raw)
        self.assertIsNone(result['cpu']['percent'])
        self.assertEqual(result['network'], {'up_bytes_sec': None, 'down_bytes_sec': None})

    def test_probe_rejects_truncated_oversized_and_wrong_protocol(self):
        for raw in (b'', PROBE_FIXTURE[:-20], b'x' * (65536 + 1), b'Linux secret response'):
            with self.subTest(size=len(raw)), self.assertRaises(self.collector.CollectorError):
                self.collector.parse_probe(raw)

    def test_unverified_host_does_not_open_ssh(self):
        host = SimpleNamespace(is_verified=False, get_ssh=Mock())
        with self.assertRaises(self.collector.CollectorError) as raised:
            self.collector.collect_host(host)
        self.assertEqual(raised.exception.code, 'host_unverified')
        host.get_ssh.assert_not_called()

    def test_verified_host_uses_fixed_probe_and_maps_safe_failures(self):
        host = SimpleNamespace(is_verified=True, get_ssh=Mock())
        with patch.object(self.collector, 'run_probe', return_value=PROBE_FIXTURE) as run:
            result = self.collector._collect_host(host)
        run.assert_called_once_with(host)
        self.assertEqual(result['cpu']['percent'], 50)
        with patch.object(self.collector, 'run_probe', side_effect=RuntimeError('private key secret')):
            with self.assertRaises(self.collector.CollectorError) as raised:
                self.collector._collect_host(host)
        self.assertNotIn('secret', str(raised.exception))

    def test_worker_timeout_kills_and_reaps_child_without_argv_credentials(self):
        child = Mock()
        child.communicate.side_effect = subprocess.TimeoutExpired('fixed worker', 15)
        with patch.object(self.collector.subprocess, 'Popen', return_value=child) as popen:
            with self.assertRaises(self.collector.CollectorError) as raised:
                self.collector.run_worker({'kind': 'panel', 'api_key': 'secret credential'})
        self.assertEqual(raised.exception.code, 'timeout')
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with()
        self.assertNotIn('secret', repr(popen.call_args))
        self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.DEVNULL)
        self.assertIn('secret credential', child.communicate.call_args.args[0].decode())

    def test_real_worker_reports_only_allowlisted_error_for_invalid_payload(self):
        with self.assertRaises(self.collector.CollectorError) as raised:
            self.collector.run_worker({'kind': 'invalid', 'api_key': 'secret credential'})
        self.assertEqual(raised.exception.code, 'collection_failed')
        self.assertNotIn('secret', str(raised.exception))

    def test_worker_is_reaped_on_communication_errors(self):
        child = Mock()
        child.communicate.side_effect = OSError('secret credential')
        child.poll.return_value = None
        with patch.object(self.collector.subprocess, 'Popen', return_value=child):
            with self.assertRaises(self.collector.CollectorError) as raised:
                self.collector.run_worker({'kind': 'invalid'})
        self.assertNotIn('secret', str(raised.exception))
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with()

    def test_real_local_http_uses_public_worker_and_single_signed_request(self):
        from apps.baota.test_connection import LocalPanel
        local = LocalPanel(response=panel_fixture())
        self.addCleanup(local.close)
        panel = SimpleNamespace(api_base_url=local.url, api_key_ciphertext='fixture',
                                get_api_key=lambda: 'local-test-secret', tls_ca='', tls_fingerprint='')
        result = self.collector.collect_panel(panel)
        self.assertEqual(result['memory']['percent'], 25)
        self.assertEqual(len(local.received), 1)
        self.assertEqual(local.received[0][0], '/system?action=GetNetWork')
        self.assertNotIn('local-test-secret', json.dumps(result))

    def test_real_slow_local_peer_is_terminated_within_worker_budget(self):
        from apps.baota.test_connection import LocalPanel
        local = LocalPanel(raw=b'{' + b' ' * 500, trickle='body')
        self.addCleanup(local.close)
        panel = SimpleNamespace(api_base_url=local.url, api_key_ciphertext='fixture',
                                get_api_key=lambda: 'local-test-secret', tls_ca='', tls_fingerprint='')
        started = time.monotonic()
        with patch.object(self.collector, 'TOTAL_TIMEOUT', 0.8):
            with self.assertRaises(self.collector.CollectorError) as raised:
                self.collector.collect_panel(panel)
        self.assertEqual(raised.exception.code, 'timeout')
        self.assertLess(time.monotonic() - started, 3)

    def test_public_ssh_worker_authenticates_saved_key_and_runs_fixed_command(self):
        local = LocalSSH()
        self.addCleanup(local.close)
        host = SimpleNamespace(is_verified=True, hostname='127.0.0.1', port=local.port,
                               username='fixture', private_key=local.private_key)
        result = self.collector.collect_host(host)
        probe = importlib.import_module('apps.baota.resource_monitor.probe')
        self.assertEqual(local.command, probe.PROBE_SCRIPT)
        self.assertEqual(result['cpu'], {'percent': 50, 'cores': 4})
        self.assertEqual(result['network']['up_bytes_sec'], 2048)

    def test_public_ssh_worker_reports_authentication_failure_safely(self):
        local = LocalSSH(authenticate=False)
        self.addCleanup(local.close)
        host = SimpleNamespace(is_verified=True, hostname='127.0.0.1', port=local.port,
                               username='fixture', private_key=local.private_key)
        with self.assertRaises(self.collector.CollectorError) as raised:
            self.collector.collect_host(host)
        self.assertEqual(raised.exception.code, 'auth_failed')
        self.assertIsNone(local.command)
        self.assertNotIn('PRIVATE', str(raised.exception))


class ProbeTests(SimpleTestCase):
    def setUp(self):
        try:
            self.probe = importlib.import_module('apps.baota.resource_monitor.probe')
        except ModuleNotFoundError:
            self.fail('resource monitor probe has not been implemented')

    def test_fixed_script_on_local_proc_fixture(self):
        # Git Bash runs the POSIX fixture on Windows; Linux CI uses /bin/sh.
        shell = '/bin/sh' if os.name != 'nt' else r'C:\Program Files\Git\bin\bash.exe'
        if not Path(shell).exists():
            self.skipTest('local POSIX shell unavailable')
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / 'net').mkdir()
            (proc / 'stat').write_text('cpu 100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3\ncpu1 1 2 3\n')
            (proc / 'net/dev').write_text('header\nheader\n lo: 500 0 0 0 0 0 0 0 500 0\n eth0: 2048 0 0 0 0 0 0 0 1024 0\n')
            (proc / 'uptime').write_text('100.0 90.0\n')
            (proc / 'meminfo').write_text('MemTotal: 8192 kB\nMemAvailable: 6144 kB\n')
            (proc / 'loadavg').write_text('0.5 1.25 2.5 1/20 100\n')
            (proc / 'mounts').write_text('/dev/vda /data\\040space ext4 rw 0 0\nserver:/disk /remote nfs rw 0 0\n')
            script = self.probe.PROBE_SCRIPT.replace('/proc/', proc.as_posix() + '/')
            script = script.replace('$(uname -s)', 'Linux')
            # Disk query stays fixed in production; fixture avoids inspecting the test machine.
            script = script.replace('timeout -k 1 3 df -Pk "$@"',
                                    'fixture_df "$@"')
            script = '''fixture_df() {
  [ "$#" -eq 1 ] && [ "$1" = '/data space' ] || exit 70
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/vda 41943040 10485760 31457280 25%% /data space\\n'
}
''' + script
            output = subprocess.run([shell], input=script.encode(), capture_output=True, timeout=10, check=True)
        collector = importlib.import_module('apps.baota.resource_monitor.collector')
        result = collector.parse_probe(output.stdout)
        self.assertEqual(result['cpu']['cores'], 2)
        self.assertIsNone(result['cpu']['percent'])  # static fixture has no CPU counter delta
        self.assertEqual(result['memory']['percent'], 25)
        self.assertEqual(result['load'], [0.5, 1.25, 2.5])
        self.assertEqual(result['disks'][0]['path'], '/data space')

    def test_stdout_and_stderr_share_output_limit_and_close_transport(self):
        channel = Mock()
        channel.recv_ready.return_value = True
        channel.recv.return_value = b'x' * 4096
        channel.recv_stderr_ready.return_value = False
        with self.assertRaises(self.probe.ProbeError) as raised:
            self.probe.read_output(channel, self.probe.Deadline(15))
        self.assertEqual(raised.exception.code, 'output_limit')

    def test_stderr_alone_is_bounded_and_never_returned(self):
        channel = Mock()
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = True
        channel.recv_stderr.return_value = b'secret' * 1000
        with self.assertRaises(self.probe.ProbeError) as raised:
            self.probe.read_output(channel, self.probe.Deadline(15))
        self.assertEqual(raised.exception.code, 'output_limit')

    def test_probe_failure_closes_channel_and_client_and_sets_all_stage_limits(self):
        channel = Mock()
        channel.exec_command.side_effect = OSError('secret remote error')
        ssh = Mock(arguments={})
        ssh.get_client.return_value.get_transport.return_value.open_session.return_value = channel
        host = SimpleNamespace(get_ssh=lambda: ssh)
        with self.assertRaises(self.probe.ProbeError) as raised:
            self.probe.run_probe(host)
        self.assertEqual(raised.exception.code, 'collection_failed')
        channel.close.assert_called_once_with()
        ssh.client.close.assert_called_once_with()
        for name in ('timeout', 'banner_timeout', 'auth_timeout', 'channel_timeout'):
            self.assertGreater(ssh.arguments[name], 0)
            self.assertLessEqual(ssh.arguments[name], 15)
        channel.exec_command.assert_called_once_with(self.probe.PROBE_SCRIPT)

    def test_exact_script_on_local_linux_proc(self):
        shell = '/bin/sh' if os.name != 'nt' else r'C:\Program Files\Git\bin\bash.exe'
        if not Path(shell).exists():
            self.skipTest('local POSIX shell unavailable')
        # Linux native CI additionally exercises the exact fixed script against local /proc.
        if os.name == 'nt':
            self.skipTest('real Linux /proc exercised on Linux CI')
        output = subprocess.run([shell], input=self.probe.PROBE_SCRIPT.encode(), capture_output=True,
                                timeout=10, check=True)
        collector = importlib.import_module('apps.baota.resource_monitor.collector')
        metrics = collector.parse_probe(output.stdout)
        self.assertGreater(metrics['cpu']['cores'], 0)
        self.assertGreater(metrics['memory']['total_bytes'], 0)

    def test_deadline_exhaustion_stops_even_continuously_ready_channel(self):
        channel = Mock()
        channel.recv_ready.return_value = True
        channel.recv.return_value = b'x'
        channel.recv_stderr_ready.return_value = False
        with patch.object(self.probe.time, 'monotonic', side_effect=[0, 1, 16]):
            with self.assertRaises(self.probe.ProbeError) as raised:
                self.probe.read_output(channel, self.probe.Deadline(15))
        self.assertEqual(raised.exception.code, 'timeout')
