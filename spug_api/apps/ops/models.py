"""Durable, bounded read-only inspections; credentials are never snapshotted."""
from django.db import models


class OpsRun(models.Model):
    job = models.CharField(max_length=40)
    job_name = models.CharField(max_length=100)
    version = models.CharField(max_length=20)
    params = models.JSONField(default=dict)
    state = models.CharField(max_length=20, default='queued', db_index=True)
    batch_size = models.PositiveSmallIntegerField(default=1)
    failure_threshold = models.PositiveSmallIntegerField(default=1)
    batch_done = models.PositiveSmallIntegerField(default=0)
    batch_failures = models.PositiveSmallIntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    created_by = models.ForeignKey('account.User', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ops_runs'
        ordering = ('-id',)


class OpsTarget(models.Model):
    run = models.ForeignKey(OpsRun, on_delete=models.PROTECT, related_name='targets')
    host = models.ForeignKey('host.Host', on_delete=models.SET_NULL, null=True, related_name='+')
    position = models.PositiveSmallIntegerField()
    host_snapshot = models.JSONField(default=dict)
    identity_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=20, default='pending', db_index=True)
    category = models.CharField(max_length=30, blank=True)
    message = models.CharField(max_length=200, blank=True)
    checks = models.JSONField(default=list)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'ops_targets'
        ordering = ('position',)
        constraints = [models.UniqueConstraint(fields=('run', 'position'), name='ops_target_position')]


class OpsAudit(models.Model):
    run = models.ForeignKey(OpsRun, on_delete=models.PROTECT, related_name='audit')
    event = models.CharField(max_length=30)
    actor_id = models.BigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict)

    class Meta:
        db_table = 'ops_audit'
        ordering = ('id',)


class ImmutableBaselineQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('基线不可修改')

    def delete(self):
        raise ValueError('基线不可删除')


class OpsBaseline(models.Model):
    name = models.CharField(max_length=100)
    job = models.CharField(max_length=40)
    version = models.CharField(max_length=20)
    params = models.JSONField(default=dict)
    host_snapshot = models.JSONField(default=dict)
    checks = models.JSONField(default=list)
    target = models.ForeignKey(OpsTarget, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey('account.User', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableBaselineQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('基线不可修改')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('基线不可删除')

    class Meta:
        db_table = 'ops_baselines'
        ordering = ('-id',)
