"""Additive monitor tables. Import through monitor.models for updatedb discovery."""
import uuid

from django.db import models


def revision():
    return uuid.uuid4().hex


class SSLSource(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    owner = models.ForeignKey('account.User', on_delete=models.PROTECT, related_name='+')
    enabled = models.BooleanField(default=False)
    reminders = models.BooleanField(default=False)
    interval_hours = models.PositiveSmallIntegerField(default=6)
    revision = models.CharField(max_length=32, default=revision)
    config_digest = models.CharField(max_length=64, default='')
    last_attempt_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=24, default='never_checked')
    message = models.CharField(max_length=200, default='尚未检查')

    class Meta:
        app_label = 'monitor'
        db_table = 'monitor_ssl_sources'


class SSLCertificate(models.Model):
    source = models.ForeignKey(SSLSource, on_delete=models.CASCADE, related_name='certificates')
    identity = models.CharField(max_length=64)
    site_id = models.CharField(max_length=512)
    site_name = models.CharField(max_length=255, default='')
    domain = models.CharField(max_length=253)
    port = models.PositiveIntegerField(default=443)
    cert_path = models.CharField(max_length=2048, default='')
    local_certificate = models.JSONField(default=dict)
    deployment_verified = models.BooleanField(default=False)
    public_tls = models.JSONField(default=dict)
    renewal = models.JSONField(default=dict)
    plan = models.JSONField(default=dict)
    config_digest = models.CharField(max_length=64)
    sampled_at = models.DateTimeField()
    present = models.BooleanField(default=True)
    latest_job = models.ForeignKey('SSLJob', null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        app_label = 'monitor'
        db_table = 'monitor_ssl_certificates'
        constraints = [models.UniqueConstraint(fields=['source', 'identity'], name='ssl_source_certificate')]


class SSLJob(models.Model):
    owner = models.ForeignKey('account.User', on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=12)
    state = models.CharField(max_length=24, default='queued', db_index=True)
    phase = models.CharField(max_length=32, default='discovery')
    message = models.CharField(max_length=200, default='等待后台处理')
    revision = models.CharField(max_length=32, default=revision)
    certificate = models.ForeignKey(SSLCertificate, null=True, on_delete=models.PROTECT, related_name='jobs')
    panel_task = models.ForeignKey('baota.PanelTask', null=True, on_delete=models.PROTECT, related_name='+')
    source_keys = models.JSONField(default=list)
    source_versions = models.JSONField(default=dict)
    server_key = models.CharField(max_length=64, default='', db_index=True)
    acknowledgements = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    plan = models.JSONField(default=dict)
    plan_digest = models.CharField(max_length=64, default='')
    baseline = models.JSONField(default=dict)
    intent = models.JSONField(default=dict)
    lease_token = models.CharField(max_length=32, default='')
    lease_until = models.DateTimeField(null=True)
    verify_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'monitor'
        db_table = 'monitor_ssl_jobs'
        ordering = ('-id',)


class SSLLease(models.Model):
    key = models.CharField(max_length=80, primary_key=True)
    job = models.ForeignKey(SSLJob, null=True, on_delete=models.SET_NULL, related_name='+')
    token = models.CharField(max_length=32, default='')
    expires_at = models.DateTimeField()

    class Meta:
        app_label = 'monitor'
        db_table = 'monitor_ssl_leases'


class SSLReminder(models.Model):
    certificate = models.ForeignKey(SSLCertificate, on_delete=models.CASCADE, related_name='reminders')
    cycle = models.CharField(max_length=64)
    stage = models.CharField(max_length=12)
    expires_at = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'monitor'
        db_table = 'monitor_ssl_reminders'
        constraints = [models.UniqueConstraint(fields=['certificate', 'cycle', 'stage'], name='ssl_reminder_cycle_stage')]


class SSLReminderPreference(models.Model):
    user = models.ForeignKey('account.User', on_delete=models.CASCADE, related_name='+')
    reminder = models.ForeignKey(SSLReminder, on_delete=models.CASCADE, related_name='preferences')
    muted = models.BooleanField(default=False)

    class Meta:
        app_label = 'monitor'
        db_table = 'monitor_ssl_reminder_preferences'
        constraints = [models.UniqueConstraint(fields=['user', 'reminder'], name='ssl_reminder_user')]
