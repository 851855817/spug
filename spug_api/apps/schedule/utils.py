# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from libs.utils import human_datetime
from libs.spug import Notification
import json
from .models import Task


def _notification_scope(task):
    from . import resources, panel_tasks
    from apps.account import access
    task = Task.objects.get(pk=task.pk)
    access.require(task.created_by_id, 'schedule.schedule.view', 'schedule.schedule.run')
    scope = dict(user=task.created_by_id, permission='schedule.schedule.view', host_ids=[], panel_ids=[])
    if task.interpreter == 'baota':
        panel_tasks.access(task.created_by_id, task, 'run')
        access.require(task.created_by_id, 'exec.panel.create', 'exec.panel.execute')
        scope['panel_ids'] = panel_tasks.definition(task)['panel_ids']
    else:
        resources.authorized(task.created_by_id, task, 'run')
        scope['host_ids'] = sorted({int(pk) for pk in json.loads(task.targets) if str(pk).isdigit()}
                                   | resources.definition_scope(task.created_by_id, task, manage=True))
        if task.latest_id:
            resources.history_access(task.created_by_id, task.latest_id, manage=True)
    return task, scope


def send_fail_notify(task, msg=None):
    try:
        task, _ = _notification_scope(task)
    except (ValueError, PermissionError, Task.DoesNotExist):
        return
    rst_notify = json.loads(task.rst_notify or '{"mode":"0"}')
    mode = rst_notify.get('mode')
    url = rst_notify.get('value')
    if mode != '0' and url:
        _do_notify(task, mode, url, msg)


def _do_notify(task, mode, url, msg):
    try:
        task, _ = _notification_scope(task)
    except (ValueError, PermissionError, Task.DoesNotExist):
        return

    def send(data, channel=None):
        try:
            current, scope = _notification_scope(task)
            saved = json.loads(current.rst_notify or '{"mode":"0"}')
            if saved.get('mode') != mode or saved.get('value') != url:
                return
        except (ValueError, PermissionError, Task.DoesNotExist):
            return
        return Notification.handle_request(url, data, channel, scope=scope)

    if mode == '1':
        texts = [
            '## <font color="#f90202">任务执行失败通知</font> ## ',
            f'**任务名称：** {task.name} ',
            f'**任务类型：** {task.type} ',
            f'**描述信息：** {msg or "请在任务计划执行历史中查看详情"} ',
            f'**发生时间：** {human_datetime()} ',
            '> 来自 Spug运维平台'
        ]
        data = {
            'msgtype': 'markdown',
            'markdown': {
                'title': '任务执行失败通知',
                'text': '\n\n'.join(texts)
            },
            'at': {
                'isAtAll': True
            }
        }
        send(data, 'dd')
    elif mode == '2':
        data = {
            'task_id': task.id,
            'task_name': task.name,
            'task_type': task.type,
            'message': msg or '请在任务计划执行历史中查看详情',
            'created_at': human_datetime()
        }
        send(data)
    elif mode == '3':
        texts = [
            '## <font color="warning">任务执行失败通知</font>',
            f'任务名称： {task.name}',
            f'任务类型： {task.type}',
            f'描述信息： {msg or "请在任务计划执行历史中查看详情"}',
            f'发生时间： {human_datetime()}',
            '> 来自 Spug运维平台'
        ]
        data = {
            'msgtype': 'markdown',
            'markdown': {
                'content': '\n'.join(texts)
            }
        }
        send(data, 'wx')
    elif mode == '4':
        data = {
            'msg_type': 'post',
            'content': {
                'post': {
                    'zh_cn': {
                        'title': '任务执行失败通知',
                        'content': [
                            [{'tag': 'text', 'text': f'任务名称： {task.name}'}],
                            [{'tag': 'text', 'text': f'任务类型： {task.type}'}],
                            [{'tag': 'text', 'text': f'描述信息： {msg or "请在任务计划执行历史中查看详情"}'}],
                            [{'tag': 'text', 'text': f'发生时间： {human_datetime()}'}],
                            [{'tag': 'at', 'user_id': 'all'}],
                        ]
                    }
                }
            }
        }
        send(data, 'fs')
