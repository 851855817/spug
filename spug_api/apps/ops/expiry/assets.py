"""Canonical local inventory. No collector, credentials, SSH or notification imports."""
import hashlib
import json
import re
from urllib.parse import urlsplit

from apps.account.models import User
from apps.host.models import Host, HostExtend, Group
from apps.baota.models import Panel, PanelGroup, ResourceMonitorPreference
from . import dates
from .models import ExpiryMetadata


KEY = re.compile(r'(host|panel):([1-9][0-9]{0,18})', re.ASCII)


def parse_key(key):
    match = KEY.fullmatch(key) if isinstance(key, str) else None
    if not match:
        raise ValueError('资产标识无效')
    return match.group(1), int(match.group(2))


def panel_address(panel):
    try:
        return (urlsplit(panel.url).hostname or '')[:255]
    except (ValueError, TypeError):
        return ''


def group_options():
    return ([{'key': 'host:%s' % g.pk, 'name': g.name,
              'parent_key': 'host:%s' % g.parent_id if g.parent_id else None}
             for g in Group.objects.order_by('pk')] +
            [{'key': 'panel:%s' % g.pk, 'name': g.name,
              'parent_key': 'panel:%s' % g.parent_id if g.parent_id else None}
             for g in PanelGroup.objects.order_by('pk')])


def descendants(key):
    parse_key(key)
    groups = group_options()
    if key not in {group['key'] for group in groups}:
        raise ValueError('分组不存在，请重新选择')
    selected = {key}
    while True:
        expanded = selected | {group['key'] for group in groups if group['parent_key'] in selected}
        if expanded == selected:
            return selected
        selected = expanded


def metadata_values(metadata):
    return {'manual_expiry': metadata.manual_expiry.isoformat() if metadata and metadata.manual_expiry else None,
            'owner_id': metadata.owner_id if metadata else None,
            'followup': metadata.followup if metadata else 'pending',
            'reminder_enabled': metadata.reminder_enabled if metadata else False,
            'exempt': metadata.exempt if metadata else False,
            'note': metadata.note if metadata else ''}


def inventory():
    """Read all assets, including unverified hosts and unconfigured/disabled panels."""
    hosts = list(Host.objects.only('id', 'name', 'hostname').prefetch_related('groups').order_by('pk'))
    panels = list(Panel.objects.only('id', 'name', 'url', 'group_id').select_related('group').order_by('pk'))
    preferences = list(ResourceMonitorPreference.objects.order_by('pk').values(
        'id', 'host_id', 'panel_id', 'linked_host_id', 'revision'))
    by_host = {h.pk: h for h in hosts}
    original = dict(HostExtend.objects.values_list('host_id', 'expired_time'))
    metadata = {m.asset_key: m for m in ExpiryMetadata.objects.all()}
    owners = {u.pk: u for u in User.objects.filter(pk__in={m.owner_id for m in metadata.values() if m.owner_id})}
    panel_preferences = {p['panel_id']: p for p in preferences if p['panel_id']}
    result = {}
    for host in hosts:
        key = 'host:%s' % host.pk
        result[key] = {'key': key, 'name': host.name, 'address': host.hostname,
                       'sources': [{'kind': 'host', 'id': host.pk, 'name': host.name}],
                       'groups': [{'key': 'host:%s' % g.pk, 'name': g.name} for g in host.groups.all()],
                       'original_expiry': original.get(host.pk), '_preferences': [], '_panel_addresses': []}
    for panel in panels:
        pref = panel_preferences.get(panel.pk)
        host_id = pref['linked_host_id'] if pref else None
        key = 'host:%s' % host_id if host_id in by_host else 'panel:%s' % panel.pk
        group = {'key': 'panel:%s' % panel.group_id, 'name': panel.group.name}
        if key not in result:
            result[key] = {'key': key, 'name': panel.name, 'address': panel_address(panel), 'sources': [],
                           'groups': [], 'original_expiry': None, '_preferences': [], '_panel_addresses': []}
        result[key]['groups'].append(group)
        result[key]['sources'].append({'kind': 'panel', 'id': panel.pk, 'name': panel.name})
        result[key]['_panel_addresses'].append([panel.pk, panel_address(panel)])
        if pref:
            result[key]['_preferences'].append(pref)
    for pref in preferences:
        if pref['host_id'] in by_host:
            result['host:%s' % pref['host_id']]['_preferences'].append(pref)
    today = dates.platform_today()
    for row in result.values():
        meta = metadata.get(row['key'])
        values = metadata_values(meta)
        row.update({k: v for k, v in values.items() if k != 'owner_id'})
        owner = owners.get(values['owner_id'])
        row['owner'] = ({'id': values['owner_id'], 'name': owner.nickname or owner.username if owner else '用户 #%s' % values['owner_id'],
                         'available': bool(owner and owner.is_active and not owner.is_deleted)} if values['owner_id'] else None)
        effective = meta.manual_expiry if meta and meta.manual_expiry else dates.parse_original(row['original_expiry'])
        row['expires_on'] = effective.isoformat() if effective else None
        row['date_source'] = 'manual' if values['manual_expiry'] else 'host_record' if row['original_expiry'] else 'unset'
        row['state'], row['days_remaining'] = dates.classify(effective, today)
        if row['original_expiry'] and effective is None:
            row['state'] = 'invalid'
        if row['exempt']:
            row['state'] = 'exempt'
        row['groups'] = list({g['key']: g for g in row['groups']}.values())
        version_inputs = [row['key'], row['name'], row['address'], row['sources'], row['groups'],
                          row['original_expiry'], row.pop('_preferences'), row.pop('_panel_addresses'), values,
                          meta.revision if meta else None, row['owner']]
        row['version'] = hashlib.sha256(json.dumps(version_inputs, sort_keys=True,
                                                  separators=(',', ':')).encode()).hexdigest()
    return result
