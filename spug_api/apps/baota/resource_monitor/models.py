"""Additive tables, imported by baota.models for updatedb model discovery."""
import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


def revision():
    return uuid.uuid4().hex


class ResourceMonitorPreference(models.Model):
    host = models.OneToOneField('host.Host', null=True, blank=True, on_delete=models.CASCADE,
                              related_name='resource_monitor_preference')
    panel = models.OneToOneField('baota.Panel', null=True, blank=True, on_delete=models.CASCADE,
                               related_name='resource_monitor_preference')
    enabled = models.BooleanField(default=False)
    linked_host = models.ForeignKey('host.Host', null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='linked_monitor_panels')
    revision = models.CharField(max_length=32, default=revision)

    class Meta:
        app_label = 'baota'
        db_table = 'baota_monitor_preferences'
        constraints = [
            models.CheckConstraint(check=(Q(host__isnull=False, panel__isnull=True) |
                                          Q(host__isnull=True, panel__isnull=False)),
                                   name='baota_monitor_one_source'),
            models.CheckConstraint(check=Q(linked_host__isnull=True) | Q(panel__isnull=False),
                                   name='baota_monitor_panel_link'),
        ]


class ResourceMonitorSnapshot(models.Model):
    # Different visible source sets cannot share a response, even for one card.
    config_digest = models.CharField(primary_key=True, max_length=64)
    card_key = models.CharField(max_length=64, db_index=True)
    target_key = models.CharField(max_length=64, db_index=True)
    metrics = models.JSONField(default=dict)
    status = models.CharField(max_length=16)
    message = models.CharField(max_length=120)
    source_kind = models.CharField(max_length=8, blank=True, default='')
    sampled_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField()
    next_attempt_at = models.DateTimeField()
    failures = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'baota'
        db_table = 'baota_monitor_snapshots'


class ResourceMonitorLease(models.Model):
    slot = models.PositiveSmallIntegerField(primary_key=True)
    target_key = models.CharField(max_length=64, null=True, unique=True)
    token = models.CharField(max_length=32, blank=True, default='')
    expires_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = 'baota'
        db_table = 'baota_monitor_leases'
        constraints = [models.CheckConstraint(check=Q(slot__in=(1, 2)), name='baota_monitor_two_slots')]
