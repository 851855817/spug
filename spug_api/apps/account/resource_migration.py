"""Credential-free legacy impact report and explicit initial policy backfill."""
import json

from django.db.models import Q

from . import access
from .models import AccountResourcePolicy, ResourceGrant, ResourceOwnership, User


ADDITIVE_MODELS = (
    ('account', 'resourcequotalock'), ('account', 'accountresourcepolicy'),
    ('account', 'resourceownership'), ('account', 'resourcegrant'),
    ('account', 'resourcegroupowner'), ('account', 'resourceaccessaudit'),
    ('alarm', 'alarmresource'), ('notify', 'notifyaudience'), ('deploy', 'deployupload'),
)


def preserved_ids(user_id, kind):
    return set(access.model(kind).objects.filter(
        Q(resource_ownership__owner_id=user_id) |
        Q(resource_ownership__isnull=True, created_by_id=user_id) |
        Q(pk__in=ResourceGrant.objects.filter(user_id=user_id).values(kind + '_id'))
    ).values_list('pk', flat=True))


def legacy_usage(hosts, panels):
    from apps.baota.resource_monitor.models import ResourceMonitorPreference
    links = dict(ResourceMonitorPreference.objects.filter(panel_id__in=panels, linked_host_id__isnull=False)
                 .values_list('panel_id', 'linked_host_id'))
    keys = {'host:%s' % pk for pk in hosts}
    keys.update('host:%s' % links[pk] if pk in links else 'panel:%s' % pk for pk in panels)
    return len(keys)


def impact_report():
    from apps.host.models import Host, Group
    from apps.baota.models import Panel, PanelTask
    from apps.baota.resource_monitor.models import ResourceMonitorPreference
    from apps.schedule.models import Task, History
    from apps.ops.models import OpsRun
    from apps.deploy.models import DeployRequest
    from apps.repository.models import Repository
    from apps.pipeline.models import PipeHistory
    policies = dict(AccountResourcePolicy.objects.values_list('user_id', 'server_limit'))
    accounts, retained = [], {}
    for user in User.objects.order_by('pk'):
        hosts, panels = preserved_ids(user.pk, 'host'), preserved_ids(user.pk, 'panel')
        retained[user.pk] = (hosts, panels)
        explicit_groups = {pk for role in user.roles.all() for pk in role.get_group_perms()
                           if type(pk) is int}
        branches = set(explicit_groups)
        while True:
            new = set(Group.objects.filter(parent_id__in=branches).values_list('pk', flat=True)) - branches
            if not new:
                break
            branches.update(new)
        old_group_hosts = set(Host.objects.filter(groups__id__in=branches).values_list('pk', flat=True))
        accounts.append({'id': user.pk, 'active': user.is_active and not user.is_deleted,
            'admin': user.is_supper, 'host_ids': sorted(hosts), 'panel_ids': sorted(panels),
            'used': legacy_usage(hosts, panels), 'has_policy': user.pk in policies,
            'limit': policies.get(user.pk), 'role_ids': sorted(user.roles.values_list('pk', flat=True)),
            'legacy_group_ids': sorted(explicit_groups),
            'legacy_group_hosts_requiring_explicit_assignment': sorted(old_group_hosts - hosts)})
    schedules = []
    for task in Task.objects.order_by('pk'):
        try:
            hosts, panels = retained[task.created_by_id]
            if task.interpreter == 'baota':
                from apps.schedule.panel_tasks import definition
                selected = set(definition(task)['panel_ids'])
                outside = sorted(selected - panels)
            else:
                selected = json.loads(task.targets)
                outside = sorted({int(x) for x in selected if str(x).isascii() and str(x).isdigit()} - hosts)
            malformed = False
        except (ValueError, TypeError, KeyError):
            outside, malformed = [], True
        schedules.append({'id': task.pk, 'creator_id': task.created_by_id,
                          'enabled': task.is_active,
                          'interpreter': task.interpreter if task.interpreter in ('sh', 'python', 'baota') else 'unsupported',
                          'out_of_scope_ids': outside, 'invalid_definition': malformed})
    return {'mode': 'report', 'accounts': accounts,
        'resources': {
            'hosts': list(Host.objects.order_by('pk').values('id', 'created_by_id')),
            'panels': list(Panel.objects.order_by('pk').values('id', 'created_by_id')),
            'ownerships': list(ResourceOwnership.objects.order_by('pk').values('host_id', 'panel_id', 'owner_id')),
            'links': list(ResourceMonitorPreference.objects.filter(panel_id__isnull=False, linked_host_id__isnull=False)
                          .order_by('pk').values('panel_id', 'linked_host_id')),
            'ordinary_creator_hosts_without_independent_key': list(Host.objects.filter(
                Q(pkey='') | Q(pkey__isnull=True), created_by__is_supper=False).order_by('pk').values_list('pk', flat=True)),
        }, 'schedules': schedules, 'in_flight': {
            'schedule_history_ids': list(History.objects.filter(status=0).values_list('pk', flat=True)),
            'panel_task_ids': list(PanelTask.objects.exclude(state__in=['cancelled', 'completed']).values_list('pk', flat=True)),
            'ops_run_ids': list(OpsRun.objects.filter(state__in=['queued', 'running', 'paused']).values_list('pk', flat=True)),
            'release_ids': list(DeployRequest.objects.filter(status='2').values_list('pk', flat=True)),
            'build_ids': list(Repository.objects.filter(status__in=['0', '1']).values_list('pk', flat=True)),
            'pipeline_history_ids': list(PipeHistory.objects.filter(status=0).values_list('pk', flat=True)),
        }}


def initialize_policies(actor):
    actor = access.admin(actor)
    created = []
    with access.quota_transaction():
        actor = access.admin(actor)
        for user in User.objects.select_for_update().filter(is_supper=False).order_by('pk'):
            if AccountResourcePolicy.objects.filter(user_id=user.pk).exists():
                continue
            used = legacy_usage(preserved_ids(user.pk, 'host'), preserved_ids(user.pk, 'panel'))
            AccountResourcePolicy.objects.create(user=user, server_limit=used)
            access.audit(actor, user, 'legacy_quota_initialized', limit=used)
            created.append(user.pk)
    return created
