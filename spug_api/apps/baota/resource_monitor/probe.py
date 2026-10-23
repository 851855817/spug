"""Fixed Linux read-only probe; transport is also isolated by collector.run_worker."""
import socket
import time

from paramiko.ssh_exception import AuthenticationException


MAX_OUTPUT_BYTES = 64 * 1024
TOTAL_TIMEOUT = 15

# No target-controlled value is inserted into this command. No shell session, SFTP,
# temporary file, package installation or process arguments are inspected remotely.
# timeout/df are optional: missing tools leave disks unknown without changing the host.
PROBE_SCRIPT = r'''PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL
if [ "$(uname -s)" != Linux ] || [ ! -r /proc/stat ] || [ ! -r /proc/meminfo ]; then
  printf 'SPUG_RESOURCE_UNSUPPORTED\n'
  exit 78
fi
printf 'SPUG_RESOURCE_V1\n'
snapshot() {
  printf 'sample\n'
  awk '$1 == "cpu" {total=0; for(i=2;i<=9;i++) total+=$i; idle=$5+$6}
       $1 ~ /^cpu[0-9]+$/ {cores++}
       END {printf "cpu\t%.0f\t%.0f\t%d\n",total,idle,cores}' /proc/stat
  awk 'FILENAME ~ /dev$/ && FNR > 2 {gsub(/:/," "); if($1 != "lo") {down+=$2; up+=$10; count++}}
       FILENAME ~ /uptime$/ {stamp=$1}
       END {if(count && stamp != "") printf "network\t%.0f\t%.0f\t%s\n",up,down,stamp;
            else print "network\t?\t?\t?"}' /proc/net/dev /proc/uptime 2>/dev/null
}
snapshot
sleep 1
snapshot
awk '$1 == "MemTotal:" {total=$2}
     $1 == "MemAvailable:" {available=$2; found=1}
     END {printf "memory\t%s\t%s\n",total == "" ? "?" : total,found ? available : "?"}' /proc/meminfo
awk '{printf "load\t%s\t%s\t%s\n",$1,$2,$3}' /proc/loadavg 2>/dev/null
if command -v timeout >/dev/null 2>&1; then
  set --
  while IFS= read -r mount; do
    [ -n "$mount" ] && set -- "$@" "$mount"
  done <<SPUG_LOCAL_MOUNTS
$(awk '$3 ~ /^(ext2|ext3|ext4|xfs|btrfs|vfat|exfat|zfs|f2fs|overlay|jfs|reiserfs)$/ && count < 32 {
    path=$2
    if(path ~ /\\011|\\012/) next
    gsub(/\\040/," ",path)
    gsub(/\\134/,"\\",path)
    if(substr(path,1,1) == "/") {print path; count++}
  }' /proc/mounts 2>/dev/null)
SPUG_LOCAL_MOUNTS
  if [ "$#" -gt 0 ]; then
    timeout -k 1 3 df -Pk "$@" 2>/dev/null |
    awk 'NR > 1 && count < 32 && match($0, /[0-9]+%[[:space:]]+/) {
      path=substr($0,RSTART+RLENGTH)
      if(substr(path,1,1) == "/") {printf "disk\t%s\t%s\t%s\t%s\n",$2,$3,$5,path; count++}
    }'
  fi
fi
printf 'SPUG_RESOURCE_END\n'
'''


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Deadline:
    def __init__(self, timeout=TOTAL_TIMEOUT):
        self.expires_at = time.monotonic() + timeout

    def remaining(self):
        value = self.expires_at - time.monotonic()
        if value <= 0:
            raise ProbeError('timeout')
        return value


def read_output(channel, deadline):
    """Drain both streams with one size/deadline budget; never retain stderr."""
    output = bytearray()
    size = 0
    while True:
        deadline.remaining()
        progressed = False
        if channel.recv_ready():
            chunk = channel.recv(4096)
            size += len(chunk)
            output.extend(chunk)
            progressed = True
        if channel.recv_stderr_ready():
            size += len(channel.recv_stderr(4096))
            progressed = True
        if size > MAX_OUTPUT_BYTES:
            raise ProbeError('output_limit')
        # Drain queued bytes before reading exit status (avoids Paramiko window deadlock).
        if not progressed and channel.exit_status_ready():
            status = channel.recv_exit_status()
            if status == 78 or bytes(output).strip() == b'SPUG_RESOURCE_UNSUPPORTED':
                raise ProbeError('unsupported')
            if status != 0:
                raise ProbeError('collection_failed')
            return bytes(output)
        if not progressed:
            time.sleep(min(0.02, deadline.remaining()))


def run_probe(host):
    deadline = Deadline()
    ssh = channel = None
    try:
        ssh = host.get_ssh()
        remaining = deadline.remaining()
        ssh.arguments.update(timeout=remaining, banner_timeout=remaining,
                             auth_timeout=remaining, channel_timeout=remaining,
                             allow_agent=False, look_for_keys=False)
        client = ssh.get_client()
        channel = client.get_transport().open_session(timeout=deadline.remaining())
        channel.settimeout(deadline.remaining())
        # The command is constant and sent directly: existing helpers that upload a
        # script or invoke an interactive login shell are deliberately not used.
        channel.exec_command(PROBE_SCRIPT)
        return read_output(channel, deadline)
    except ProbeError:
        raise
    except AuthenticationException:
        raise ProbeError('auth_failed') from None
    except (socket.timeout, TimeoutError):
        raise ProbeError('timeout') from None
    except Exception:
        raise ProbeError('collection_failed') from None
    finally:
        if channel is not None:
            channel.close()
        if ssh is not None and ssh.client is not None:
            ssh.client.close()
