# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.db import models
from django.core.cache import cache
from libs import ModelMixin, human_datetime
from libs.channel import Channel
import hashlib
from apps.account.models import User


class Notify(models.Model, ModelMixin):
    TYPES = (
        ('1', '通知'),
        ('2', '待办'),
    )
    SOURCES = (
        ('monitor', '监控中心'),
        ('schedule', '任务计划'),
        ('flag', '应用发布'),
        ('alert', '系统警告'),
    )
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=10, choices=SOURCES)
    type = models.CharField(max_length=2, choices=TYPES)
    content = models.TextField(null=True)
    unread = models.BooleanField(default=True)
    link = models.CharField(max_length=255, null=True)

    created_at = models.CharField(max_length=20, default=human_datetime)

    @classmethod
    def make_system_notify(cls, title, content, **scope):
        return cls._make_notify('alert', '1', title, content, **scope)

    @classmethod
    def make_monitor_notify(cls, title, content, **scope):
        return cls._make_notify('monitor', '1', title, content, **scope)

    @classmethod
    def make_schedule_notify(cls, title, content, **scope):
        return cls._make_notify('schedule', '1', title, content, **scope)

    @classmethod
    def make_deploy_notify(cls, title, content, **scope):
        return cls._make_notify('flag', '1', title, content, **scope)

    @classmethod
    def _make_notify(cls, source, type, title, content, *, user=None, permission='', host_ids=None, panel_ids=None):
        from apps.account import access
        current = None
        host_ids, panel_ids = host_ids or [], panel_ids or []
        if user is not None:
            current = access.require(user, permission) if permission else access.current_user(user)
            access.require_resources(current, 'host', host_ids)
            access.require_resources(current, 'panel', panel_ids)
        tmp_str = f'{getattr(current, "pk", None)},{permission},{sorted(host_ids)},{sorted(panel_ids)},{source},{type},{title},{content}'
        digest = hashlib.md5(tmp_str.encode()).hexdigest()
        unique_key = f'spug:notify:{digest}'
        if not cache.add(unique_key, 1, 3600):
            return
        from django.db import transaction
        with transaction.atomic():
            row = cls.objects.create(source=source, title=title, type=type, content=content)
            metadata = {}
            if current:
                NotifyAudience.objects.create(notification=row, user=current, permission=permission,
                    host_ids=host_ids, panel_ids=panel_ids)
                metadata = dict(resource_owner_id=current.pk, resource_permission=permission,
                    resource_host_ids=host_ids, resource_panel_ids=panel_ids, notification_id=row.pk)
            transaction.on_commit(lambda: Channel.send_notify(title, content, **metadata))
        return row

    def __repr__(self):
        return '<Notify %r>' % self.title

    class Meta:
        db_table = 'notifies'
        ordering = ('-id',)


class NotifyAudience(models.Model):
    """An explicit recipient and target scope; legacy unknown events stay admin-only."""
    notification = models.ForeignKey(Notify, on_delete=models.CASCADE, related_name='audience')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    permission = models.CharField(max_length=100, default='')
    host_ids = models.JSONField(default=list)
    panel_ids = models.JSONField(default=list)
    unread = models.BooleanField(default=True)

    class Meta:
        db_table = 'notify_resource_audience'
        constraints = [models.UniqueConstraint(fields=['notification', 'user'], name='unique_notify_resource_recipient')]
