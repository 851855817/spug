# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.conf import settings
from django_redis import get_redis_connection
from libs.utils import human_seconds_time, wrap_python_command
from libs.ssh import SSH
from libs.locale import translate_console
from apps.account import access
from apps.account.utils import require_owned_task
import threading
import socket
import json
import time


def exec_worker_handler(job):
    job = Job(**json.loads(job))
    threading.Thread(target=job.run).start()


class Job:
    def __init__(self, token, key, name, hostname, port, username, pkey, command, interpreter, params=None,
                 term=None, language='zh', record=False, actor_id=None, template_id=None):
        from apps.exec.models import ExecHistory, ExecTemplate
        self.token, self.key = token, key
        self.task = ExecHistory.objects.filter(digest=token).first()
        self.template = None
        if self.task:
            self.actor_id = actor_id or self.task.user_id
            require_owned_task(self.actor_id, self.task, 'exec.task.do')
            if key not in json.loads(self.task.host_ids):
                access.reject(self.actor_id, '任务目标无效')
        elif record and template_id:
            self.template = ExecTemplate.objects.filter(pk=template_id).first()
            if not self.template:
                raise access.AccessDenied('执行模板不存在')
            self.actor_id = self.template.created_by_id
            access.require(self.actor_id, 'exec.task.do')
            access.require_resources(self.actor_id, 'host', json.loads(self.template.host_ids), manage=True)
            if key not in json.loads(self.template.host_ids):
                access.reject(self.actor_id, '任务目标无效')
        else:
            raise access.AccessDenied('缺少有效执行归属')
        host = access.require_resources(self.actor_id, 'host', [key], manage=True).get()
        self.ssh = SSH(host.hostname, host.port, host.username, host.private_key, term=term,
                       authorize=self._authorize)
        self.key = key
        self.command = self._handle_command(command, interpreter)
        self.token = token
        self.rds = get_redis_connection()
        self.rds_key = f'PID:{self.token}:{self.key}'
        # 开放 API 触发的任务没有 WebSocket 订阅者，需把输出与状态落到 redis 供结果接口查询
        self.record_key = f'{settings.EXEC_RESULT_KEY}:{self.token}' if record else None
        # 发起执行的用户界面语言，仅用于翻译 Spug 自身产生的提示，命令输出原样透传
        self.language = language
        self.env = dict(
            SPUG_HOST_ID=str(self.key),
            SPUG_HOST_NAME=name,
            SPUG_HOST_HOSTNAME=hostname,
            SPUG_SSH_PORT=str(port),
            SPUG_SSH_USERNAME=username,
            SPUG_INTERPRETER=interpreter
        )
        if isinstance(params, dict):
            self.env.update({f'_SPUG_{k}': str(v) for k, v in params.items()})

    def _send(self, message):
        self.rds.publish(self.token, json.dumps(message))
        if self.record_key:
            if message.get('data'):
                self.rds.append(f'{self.record_key}:{self.key}', message['data'])
                self.rds.expire(f'{self.record_key}:{self.key}', 3600)
            if 'status' in message:
                self.rds.hset(self.record_key, f'status:{self.key}', message['status'])

    def _handle_command(self, command, interpreter):
        if interpreter == 'python':
            return wrap_python_command(command)
        return command

    def send(self, data):
        self._send({'key': self.key, 'data': data})

    def send_status(self, code):
        self._send({'key': self.key, 'status': code})

    def _authorize(self):
        access.require(self.actor_id, 'exec.task.do')
        if self.task:
            self.task.refresh_from_db()
            require_owned_task(self.actor_id, self.task, 'exec.task.do')
            if self.key not in json.loads(self.task.host_ids):
                access.reject(self.actor_id, '任务目标无效')
        elif self.template:
            self.template.refresh_from_db()
            access.require_resources(self.actor_id, 'host', json.loads(self.template.host_ids), manage=True)
            if self.key not in json.loads(self.template.host_ids):
                access.reject(self.actor_id, '任务目标无效')
        access.require_resources(self.actor_id, 'host', [self.key], manage=True)

    def run(self):
        self._authorize()
        flag = time.time()
        self.send('\r\n\x1b[36m### Executing ...\x1b[0m\r\n')
        code = -1
        try:
            self._authorize()
            with self.ssh:
                self._authorize()
                pid = self.ssh.get_pid()
                if pid:
                    self.rds.set(self.rds_key, pid, 3600)
                self._authorize()
                for code, out in self.ssh.exec_command_with_stream(self.command, self.env):
                    self.send(out)
            human_time = human_seconds_time(time.time() - flag)
            self.send(translate_console(f'\r\n\x1b[36m** 执行结束，耗时：{human_time} **\x1b[0m', self.language))
        except socket.timeout:
            code = 130
            self.send('\r\n\x1b[31m### Time out\x1b[0m')
        except Exception as e:
            code = 131
            self.send(f'\r\n\x1b[31m### Exception {e}\x1b[0m')
            raise e
        finally:
            self.rds.delete(self.rds_key)
            self.send_status(code)
