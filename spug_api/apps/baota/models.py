import uuid

from django.db import models

from apps.account.models import User
from libs import ModelMixin, human_datetime
from .security import decrypt_secret


class PanelGroup(models.Model, ModelMixin):
    name = models.CharField(max_length=50)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')

    def to_view(self):
        return {'id': self.id, 'name': self.name, 'parent_id': self.parent_id}

    class Meta:
        db_table = 'baota_groups'
        ordering = ('id',)
        constraints = [models.UniqueConstraint(fields=['parent', 'name'], name='baota_group_parent_name')]


class Panel(models.Model, ModelMixin):
    name = models.CharField(max_length=100)
    url = models.CharField(max_length=2048)
    username = models.CharField(max_length=100)
    password_ciphertext = models.TextField()
    api_base_url = models.CharField(max_length=2048, blank=True, default='')
    api_key_ciphertext = models.TextField(blank=True, default='')
    tls_ca = models.TextField(blank=True, default='')
    tls_fingerprint = models.CharField(max_length=64, blank=True, default='')
    api_checked_at = models.CharField(max_length=20, blank=True, default='')
    api_version = models.CharField(max_length=64, blank=True, default='')
    api_config_revision = models.PositiveIntegerField(default=0)
    group = models.ForeignKey(PanelGroup, on_delete=models.PROTECT, related_name='panels')
    desc = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    created_at = models.CharField(max_length=20, default=human_datetime)

    def to_view(self):
        data = self.to_dict(excludes=('password_ciphertext', 'api_key_ciphertext', 'tls_ca'))
        data['has_password'] = bool(self.password_ciphertext)
        data['has_api_key'] = bool(self.api_key_ciphertext)
        data['has_tls_ca'] = bool(self.tls_ca)
        return data

    def get_password(self):
        return decrypt_secret(self.password_ciphertext)

    def get_api_key(self):
        return decrypt_secret(self.api_key_ciphertext) if self.api_key_ciphertext else ''

    class Meta:
        db_table = 'baota_panels'
        ordering = ('-id',)


class PanelTask(models.Model):
    operation = models.CharField(max_length=40)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name='panel_tasks')
    params_by_panel = models.JSONField(default=dict)
    state = models.CharField(max_length=24, default='preflight_queued', db_index=True)
    retry_parent = models.ForeignKey('self', null=True, on_delete=models.PROTECT, related_name='retries')
    acknowledgements = models.JSONField(default=list)
    audit = models.JSONField(default=list)
    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'baota_tasks'
        ordering = ('-id',)


class PanelTaskTarget(models.Model):
    task = models.ForeignKey(PanelTask, on_delete=models.PROTECT, related_name='targets')
    panel = models.ForeignKey(Panel, on_delete=models.PROTECT, related_name='task_targets')
    panel_snapshot = models.JSONField(default=dict)
    params = models.JSONField(default=dict)
    children = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    state = models.CharField(max_length=24, default='preflight_queued', db_index=True)
    message = models.CharField(max_length=500, default='等待只读预检')
    reconcile_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'baota_task_targets'
        ordering = ('id',)
        constraints = [models.UniqueConstraint(fields=['task', 'panel'], name='baota_task_panel_unique')]


class PanelLock(models.Model):
    # The unique panel key, rather than process memory, is the serialization authority.
    panel = models.OneToOneField(Panel, primary_key=True, on_delete=models.PROTECT, related_name='task_lock')
    target = models.OneToOneField(PanelTaskTarget, on_delete=models.PROTECT, related_name='panel_lock')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'baota_panel_locks'


def upload_id():
    return uuid.uuid4().hex


class PanelUploadBundle(models.Model):
    id = models.CharField(primary_key=True, max_length=32, default=upload_id, editable=False)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name='panel_upload_bundles')
    state = models.CharField(max_length=16, default='draft', choices=[(s, s) for s in ('draft', 'sealed', 'expired')])
    total_bytes = models.BigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    manifest_sha256 = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'baota_upload_bundles'


class PanelUploadFile(models.Model):
    id = models.CharField(primary_key=True, max_length=32, default=upload_id, editable=False)
    bundle = models.ForeignKey(PanelUploadBundle, on_delete=models.PROTECT, related_name='files')
    relative_path = models.CharField(max_length=1024)
    # utf8mb4 paths cannot fit MariaDB's 3072-byte composite index limit.
    path_digest = models.CharField(max_length=64)
    size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, blank=True, default='')
    state = models.CharField(max_length=16, default='pending', choices=[(s, s) for s in ('pending', 'receiving', 'complete')])
    receive_token = models.CharField(max_length=32, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'baota_upload_files'
        constraints = [models.UniqueConstraint(fields=['bundle', 'path_digest'], name='baota_upload_bundle_path')]


class PanelUploadBinding(models.Model):
    target = models.OneToOneField(PanelTaskTarget, on_delete=models.PROTECT, related_name='upload_binding')
    bundle = models.ForeignKey(PanelUploadBundle, on_delete=models.PROTECT, related_name='bindings')

    class Meta:
        db_table = 'baota_upload_bindings'


class PanelUploadResult(models.Model):
    target = models.ForeignKey(PanelTaskTarget, on_delete=models.PROTECT, related_name='upload_files')
    source = models.ForeignKey(PanelUploadFile, on_delete=models.PROTECT, related_name='results')
    state = models.CharField(max_length=24, default='ready', choices=[(s, s) for s in
                            ('ready', 'failed', 'dispatching', 'success', 'uncertain', 'cancelled')])
    before = models.JSONField(default=dict)
    intent = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    resolutions = models.JSONField(default=list)
    message = models.CharField(max_length=500, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'baota_upload_results'
        constraints = [models.UniqueConstraint(fields=['target', 'source'], name='baota_upload_target_source')]


class PanelUploadQuota(models.Model):
    # A single durable row serializes quota reservations across API processes.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        db_table = 'baota_upload_quota'
