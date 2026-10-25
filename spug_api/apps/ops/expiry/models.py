"""Local expiry records; asset keys and snapshots deliberately survive asset deletion."""
import uuid

from django.db import models


def revision():
    return uuid.uuid4().hex


class ExpiryMetadata(models.Model):
    asset_key = models.CharField(primary_key=True, max_length=40)
    manual_expiry = models.DateField(null=True)
    # Retain the identifier when an account is removed; assignment grants no ACL.
    owner_id = models.BigIntegerField(null=True)
    followup = models.CharField(max_length=16, default='pending')
    reminder_enabled = models.BooleanField(default=False)
    exempt = models.BooleanField(default=False)
    note = models.CharField(max_length=2000, default='', blank=True)
    revision = models.CharField(max_length=32, default=revision)

    class Meta:
        app_label = 'ops'
        db_table = 'ops_expiry_metadata'


class ExpiryAudit(models.Model):
    asset_key = models.CharField(max_length=40, db_index=True)
    event = models.CharField(max_length=16)
    actor_id = models.BigIntegerField(null=True)
    actor_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict)

    class Meta:
        app_label = 'ops'
        db_table = 'ops_expiry_audit'
        ordering = ('-id',)


class ExpiryReminder(models.Model):
    asset_key = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    expires_on = models.DateField()
    stage = models.CharField(max_length=8)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'ops'
        db_table = 'ops_expiry_reminders'
        ordering = ('-id',)
        constraints = [models.UniqueConstraint(fields=('asset_key', 'expires_on', 'stage'),
                                               name='ops_expiry_reminder_cycle')]


class ExpiryReminderRead(models.Model):
    reminder = models.ForeignKey(ExpiryReminder, on_delete=models.CASCADE, related_name='+')
    user = models.ForeignKey('account.User', on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'ops'
        db_table = 'ops_expiry_reminder_reads'
        constraints = [models.UniqueConstraint(fields=('reminder', 'user'), name='ops_expiry_personal_read')]


class ExpiryCheck(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default='never')
    last_attempt_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    message = models.CharField(max_length=200, blank=True, default='尚未执行每日检查')

    class Meta:
        app_label = 'ops'
        db_table = 'ops_expiry_check'


class ExpiryLock(models.Model):
    """One durable row serializes metadata and local reminder decisions across processes."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        app_label = 'ops'
        db_table = 'ops_expiry_lock'
