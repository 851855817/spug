# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from libs.ssh import AuthenticationException
from django.db import close_old_connections, transaction
from apps.host.models import Host
from apps.schedule.models import History, Task
from apps.schedule.utils import send_fail_notify
from libs.utils import wrap_python_command
import subprocess
import socket
import time
import json


def local_executor(command):
    code, out, now = 1, None, time.time()
    task = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        code = task.wait(3600)
        out = task.stdout.read() + task.stderr.read()
        out = out.decode()
    except subprocess.TimeoutExpired:
        # task.kill()
        out = 'timeout, wait more than 1 hour'
    return code, round(time.time() - now, 3), out


def host_executor(host, command, authorize=None):
    code, out, now = 1, None, time.time()
    try:
        with host.get_ssh(authorize=authorize) as ssh:
            code, out = ssh.exec_command_raw(command)
    except AuthenticationException:
        out = 'ssh authentication fail'
    except socket.error as e:
        out = f'network error {e}'
    return code, round(time.time() - now, 3), out


def dispatch_job(host_id, interpreter, command, authorize=None):
    if interpreter not in ('sh', 'python'):
        return 1, 0, '非脚本任务不能交给 Shell 执行器'
    if authorize is None:
        raise PermissionError('缺少计划执行授权')
    authorize()
    if interpreter == 'python':
        command = wrap_python_command(command)
    if host_id == 'local':
        authorize()
        code, duration, out = local_executor(command)
    else:
        host = Host.objects.filter(pk=host_id).first()
        if not host:
            code, duration, out = 1, 0, f'unknown host id for {host_id!r}'
        else:
            code, duration, out = host_executor(host, command, authorize=authorize)
    return code, duration, out


def schedule_worker_handler(job):
    history_id, host_id, interpreter, command = json.loads(job)
    record = History.objects.filter(pk=history_id).first()
    current = Task.objects.filter(pk=record.task_id).first() if record else None
    if record is None or record.status != 0:
        return
    from . import resources
    selected = list(json.loads(record.output))
    if str(host_id) not in {str(x) for x in selected}:
        return
    if not current or current.interpreter != interpreter or current.interpreter == 'baota':
        resources.fail_history(record.pk, '计划内容或解释器已变化，未继续执行；已有调用需核验')
        return
    denied = False
    def authorize():
        # This callback also runs inside every SSH boundary. A coordinator may
        # have closed the history after a prefix was queued but before connect.
        return resources.runtime_for_history(history_id, current.pk, host_id, interpreter, command)

    try:
        authorize()
    except (ValueError, PermissionError, Task.DoesNotExist):
        denied = True
        code, duration, out = 1, 0, '计划授权或执行目标已变化，未发送命令'
    else:
        try:
            code, duration, out = dispatch_job(host_id, interpreter, command, authorize=authorize)
        except (ValueError, PermissionError, Task.DoesNotExist):
            denied = True
            code, duration, out = 1, 0, '计划授权或执行目标已变化，未发送命令'

    close_old_connections()
    with transaction.atomic():
        history = History.objects.select_for_update().get(pk=history_id)
        if history.status != 0 or history.task_id != current.pk:
            return
        output = json.loads(history.output)
        output[str(host_id)] = [code, duration, out]
        if denied:
            output = {key: value or [code, duration, out] for key, value in output.items()}
        history.output = json.dumps(output)
        if all(output.values()):
            history.status = '1' if sum(x[0] for x in output.values()) == 0 else '2'
        history.save()
    if history.status == '2':
        task = Task.objects.get(pk=history.task_id)
        send_fail_notify(task)
