# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.conf import settings
from django_redis import get_redis_connection
from asgiref.sync import async_to_sync
from apps.host.models import Host
from consumer.utils import BaseConsumer
from apps.account.utils import require_stream
from apps.account import access
from libs.utils import str_decode
from threading import Thread
import time
import json
import socket


class ComConsumer(BaseConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key = None
        self.rds = None
        self.module = None

    def authorize(self):
        super().authorize()
        require_stream(self.user, self.key, self.rds, module=self.module)

    def init(self):
        # scope is only available after the connection is accepted (channels 3+)
        token = self.scope['url_route']['kwargs']['token']
        module = self.scope['url_route']['kwargs']['module']
        self.module = module
        if module == 'build':
            self.key = f'{settings.BUILD_KEY}:{token}'
        elif module == 'request':
            self.key = f'{settings.REQUEST_KEY}:{token}'
        elif module in ('pipeline', 'host', 'raw'):
            # the token already is the full redis key
            self.key = token
        else:
            raise TypeError(f'unknown module for {module}')
        self.rds = get_redis_connection()
        self.check_access()

    def disconnect(self, code):
        if self.rds:
            self.rds.close()

    def get_response(self, index):
        counter = 0
        while counter < 30:
            if not self.check_access():
                return None
            response = self.rds.lindex(self.key, index)
            if response:
                return response.decode()
            counter += 1
            time.sleep(0.2)

    def receive(self, text_data='', **kwargs):
        if not self.rds or not self.check_access():
            return
        if text_data.isdigit():
            index = int(text_data)
            response = self.get_response(index)
            while response and not self.closed:
                index += 1
                self.send(text_data=response)
                response = self.get_response(index)
        self.send(text_data='pong')


class SSHConsumer(BaseConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.id = None
        self.chan = None
        self.ssh = None

    def authorize(self):
        super().authorize()
        access.require(self.user, 'host.console.view')
        access.require_resources(self.user, 'host', [int(self.id)], manage=True)

    def loop_read(self):
        try:
            self._loop_read()
        except access.AccessDenied:
            self.check_access()
        finally:
            self.disconnect(None)

    def _loop_read(self):
        is_ready, buf_size = False, 4096
        while not self.closed:
            if not self.check_access():
                break
            try:
                data = self.chan.recv(buf_size)
            except socket.timeout:
                continue
            if not self.check_access():
                break
            if not data:
                self.close(3333)
                break
            while self.chan.recv_ready():
                if not self.check_access():
                    return
                data += self.chan.recv(buf_size)
            try:
                text = data.decode()
            except UnicodeDecodeError:
                try:
                    text = data.decode(encoding='GBK')
                except UnicodeDecodeError:
                    text = data.decode(errors='ignore')

            if not is_ready:
                self.send(text_data='\033[2J\033[3J\033[1;1H')
                is_ready = True
            self.send(text_data=text)

    def receive(self, text_data=None, bytes_data=None):
        if not self.check_access():
            self.disconnect(None)
            return
        data = text_data or bytes_data
        if data and self.chan:
            data = json.loads(data)
            # print('write: {!r}'.format(data))
            resize = data.get('resize')
            try:
                if resize and len(resize) == 2:
                    self.chan.resize_pty(*resize)
                else:
                    self.chan.send(data['data'])
            except access.AccessDenied:
                self.check_access()
                self.disconnect(None)

    def disconnect(self, code):
        if self.chan:
            self.chan.close()
        if self.ssh:
            self.ssh.close()

    def init(self):
        self.id = self.scope['url_route']['kwargs']['id']
        if self.check_access():
            self.send(text_data='\r\n正在连接至主机 ...')
            host = Host.objects.filter(pk=self.id).first()
            if not host:
                return self.close_with_message('未找到指定主机，请刷新页面重试。')

            try:
                self.ssh = host.get_ssh(authorize=self.authorize).get_client()
                if not self.check_access():
                    self.disconnect(None)
                    return
                self.chan = self.ssh.invoke_shell(term='xterm')
                self.chan.settimeout(1)
                self.chan.transport.set_keepalive(30)
                Thread(target=self.loop_read).start()
            except access.AccessDenied:
                self.check_access()
                self.disconnect(None)
            except Exception as e:
                self.disconnect(None)
                return self.close_with_message(f'连接主机失败: {e}')
        else:
            self.close_with_message('你当前无权限操作该主机，请联系管理员授权。')


class NotifyConsumer(BaseConsumer):
    def init(self):
        async_to_sync(self.channel_layer.group_add)('notify', self.channel_name)

    def disconnect(self, code):
        async_to_sync(self.channel_layer.group_discard)('notify', self.channel_name)

    def receive(self, **kwargs):
        self.send(text_data='pong')

    def notify_message(self, event):
        if not self.check_access():
            return
        if not self.user.is_supper:
            if event.get('resource_owner_id') != self.user.pk or 'resource_host_ids' not in event:
                return
            try:
                access.require(self.user, event.get('resource_permission', 'notify.notify.view'))
                access.require_resources(self.user, 'host', event['resource_host_ids'])
            except ValueError:
                return
        self.send(text_data=json.dumps(event))


class PubSubConsumer(BaseConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = None
        self.rds = None
        self.p = None

    def authorize(self):
        super().authorize()
        require_stream(self.user, self.token, self.rds)

    def init(self):
        self.token = self.scope['url_route']['kwargs']['token']
        self.rds = get_redis_connection()
        if not self.check_access():
            return
        self.p = self.rds.pubsub(ignore_subscribe_messages=True)
        self.p.subscribe(self.token)

    def disconnect(self, code):
        if self.p:
            self.p.close()
        if self.rds:
            self.rds.close()

    def receive(self, **kwargs):
        if not self.p or not self.check_access():
            return
        response = self.p.get_message(timeout=1)
        while response and not self.closed:
            if not self.check_access():
                break
            data = str_decode(response['data'])
            self.send(text_data=data)
            response = self.p.get_message(timeout=1)
        self.send(text_data='pong')
