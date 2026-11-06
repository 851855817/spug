"""Additive account policy tables; creation audit on assets remains immutable."""
from django.db import models
from django.db.models import Q


class ResourceQuotaLock(models.Model):
    """Single short transaction lock for changes to the quota resource graph."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        app_label = 'account'
        db_table = 'account_resource_quota_lock'


class AccountResourcePolicy(models.Model):
    user = models.OneToOneField('account.User', primary_key=True, on_delete=models.CASCADE,
                                related_name='resource_policy')
    # None means explicitly unlimited; missing policies and new accounts mean zero.
    server_limit = models.PositiveIntegerField(default=0, null=True)

    class Meta:
        app_label = 'account'
        db_table = 'account_resource_policies'


ONE_RESOURCE = (Q(host__isnull=False, panel__isnull=True) | Q(host__isnull=True, panel__isnull=False))


class ResourceOwnership(models.Model):
    host = models.OneToOneField('host.Host', null=True, on_delete=models.CASCADE, related_name='resource_ownership')
    panel = models.OneToOneField('baota.Panel', null=True, on_delete=models.CASCADE, related_name='resource_ownership')
    owner = models.ForeignKey('account.User', on_delete=models.PROTECT, related_name='resource_ownerships')

    class Meta:
        app_label = 'account'
        db_table = 'account_resource_ownerships'
        constraints = [models.CheckConstraint(check=ONE_RESOURCE, name='account_ownership_one_resource')]


class ResourceGrant(models.Model):
    user = models.ForeignKey('account.User', on_delete=models.CASCADE, related_name='resource_grants')
    host = models.ForeignKey('host.Host', null=True, on_delete=models.CASCADE, related_name='resource_grants')
    panel = models.ForeignKey('baota.Panel', null=True, on_delete=models.CASCADE, related_name='resource_grants')
    level = models.CharField(max_length=8, choices=(('read', 'read'), ('manage', 'manage')))

    class Meta:
        app_label = 'account'
        db_table = 'account_resource_grants'
        constraints = [models.CheckConstraint(check=ONE_RESOURCE, name='account_grant_one_resource'),
                       models.CheckConstraint(check=Q(level__in=('read', 'manage')), name='account_grant_level'),
                       models.UniqueConstraint(fields=('user', 'host'), name='account_grant_user_host'),
                       models.UniqueConstraint(fields=('user', 'panel'), name='account_grant_user_panel')]


class ResourceGroupOwner(models.Model):
    kind = models.CharField(max_length=8)
    group_id = models.PositiveBigIntegerField()
    owner = models.ForeignKey('account.User', on_delete=models.PROTECT, related_name='+')

    class Meta:
        app_label = 'account'
        db_table = 'account_resource_group_owners'
        constraints = [models.UniqueConstraint(fields=('kind', 'group_id'), name='account_owned_group'),
                       models.CheckConstraint(check=Q(kind__in=('host', 'panel')), name='account_group_kind')]


class ResourceAccessAudit(models.Model):
    actor = models.ForeignKey('account.User', null=True, on_delete=models.SET_NULL, related_name='+')
    account = models.ForeignKey('account.User', null=True, on_delete=models.SET_NULL, related_name='+')
    event = models.CharField(max_length=40)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'account'
        db_table = 'account_resource_audit'
