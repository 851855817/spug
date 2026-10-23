"""Release checks inside the app; no collection, no customer configuration output."""
import os
import sys

sys.path.insert(0, '/data/spug/spug_api')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spug.settings')
import django
django.setup()
from django.db import connection
from apps.baota.models import Panel
from apps.host.models import Host
from apps.baota.resource_monitor.models import ResourceMonitorPreference as Preference
from apps.baota.resource_monitor.models import ResourceMonitorSnapshot as Snapshot
from apps.baota.resource_monitor.models import ResourceMonitorLease as Lease

tables = connection.introspection.table_names()
assert all(model._meta.db_table in tables for model in (Preference, Snapshot, Lease))
assert Preference._meta.get_field('enabled').default is False
mode = sys.argv[1] if len(sys.argv) > 1 else 'empty'
if mode == 'seed':
    assert not Preference.objects.exists() and not Snapshot.objects.exists()
    source = Panel.objects.first() or Host.objects.first()
    assert source is not None, 'Clone must include a source for preservation rehearsal'
    field = 'panel' if isinstance(source, Panel) else 'host'
    preference = Preference.objects.create(**{field: source})
    assert preference.enabled is False
    Preference.objects.filter(pk=preference.pk).update(revision='monitor-rehearsal-marker')
elif mode == 'retained':
    assert Preference.objects.filter(revision='monitor-rehearsal-marker', enabled=False).count() == 1
elif mode == 'clear':
    assert Preference.objects.filter(revision='monitor-rehearsal-marker', enabled=False).count() == 1
    Preference.objects.filter(revision='monitor-rehearsal-marker').delete()
else:
    assert mode == 'empty'
    assert not Preference.objects.exists() and not Snapshot.objects.exists() and not Lease.objects.exists()
print('Monitor tables, default OFF and release state verified: ' + mode)
