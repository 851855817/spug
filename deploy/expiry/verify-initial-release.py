"""Read-only acceptance against the real instance; no jobs or example rows."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, '/data/spug/spug_api')
os.environ['DJANGO_SETTINGS_MODULE'] = 'spug.settings'
import django
django.setup()
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory
from apps.account.models import User
from apps.host.models import Host
from apps.baota.models import Panel, ResourceMonitorPreference
from apps.ops.expiry import service
from apps.ops.expiry.views import OptionsView, AssetsView, RemindersView
from apps.setting.health_views import PlatformHealthView

factory = RequestFactory()
admin = User.objects.filter(is_supper=True, is_active=True, is_deleted=False).first()
assert admin is not None
models = [model for model in apps.get_app_config('ops').get_models() if model.__name__.startswith('Expiry')]
before = {model.__name__: model.objects.count() for model in models}
assert len(before) == 6 and not any(before.values())

def get(view, path, user):
    request = factory.get(path)
    request.user = user
    return json.loads(view.as_view()(request).content)

for view in (OptionsView, AssetsView, RemindersView):
    assert not get(view, '/', admin)['error']
    assert get(view, '/', SimpleNamespace(pk=None, is_supper=False))['error']

listing = service.listing(admin, {'page_size': '100'})
linked = ResourceMonitorPreference.objects.filter(linked_host_id__in=Host.objects.values('id'),
    panel_id__in=Panel.objects.values('id')).values('panel_id').distinct().count()
assert listing['total'] == Host.objects.count() + Panel.objects.count() - linked
assert not any(row['reminder_enabled'] for row in listing['items'])
assert {model.__name__: model.objects.count() for model in models} == before
executor = MigrationExecutor(connection)
assert not executor.migration_plan(executor.loader.graph.leaf_nodes())
health = get(PlatformHealthView, '/', admin)
assert not health['error']
checks = health['data']['checks']
assert len(checks) == 15 and all(check['status'] == 'ok' for check in checks), {
    check['id']: check['status'] for check in checks}
print(json.dumps({'expiry_assets': listing['total'], 'confirmed_linked_panels': linked,
    'expiry_tables': len(models), 'get_preserves_empty_tables': True, 'reminders_default_off': True,
    'admin_only': True, 'migrations_complete': True, 'health': health['data']['status'],
    'health_checks_ok': len(checks)}))
