"""Additive local snapshots and one cross-process collection lease."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PanelInventorySnapshot(models.Model):
    panel = models.ForeignKey('baota.Panel', on_delete=models.CASCADE, related_name='inventory_snapshots')
    kind = models.CharField(max_length=12)
    config_revision = models.PositiveIntegerField(default=0)
    config_digest = models.CharField(max_length=64, default='')
    rows = models.JSONField(default=list)
    status = models.CharField(max_length=16, default='error')
    message = models.CharField(max_length=160, default='')
    sampled_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField(null=True)
    cooldown_until = models.DateTimeField(null=True)

    class Meta:
        app_label = 'baota'
        db_table = 'baota_inventory_snapshots'
        constraints = [models.UniqueConstraint(fields=['panel', 'kind'], name='baota_inventory_panel_kind'),
                       models.CheckConstraint(check=Q(kind__in=('sites', 'backups')), name='baota_inventory_kind')]


class PanelInventoryLease(models.Model):
    slot = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    token = models.CharField(max_length=32, default='')
    expires_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = 'baota'
        db_table = 'baota_inventory_leases'
        constraints = [models.CheckConstraint(check=Q(slot=1), name='baota_inventory_one_slot')]
