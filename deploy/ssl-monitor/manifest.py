"""Fixed reviewed runtime surface for the first SSL monitoring release."""

API_FILES = (
    'apps/monitor/models.py', 'apps/monitor/urls.py', 'apps/schedule/scheduler.py',
    'apps/baota/worker.py',
    *('apps/monitor/ssl/' + name for name in (
        '__init__.py', 'models.py', 'service.py', 'jobs.py', 'renewal.py',
        'views.py', 'urls.py', 'collectors.py', 'remote_probe.py', 'probe_worker.py', 'tls.py')),
)
COMPAT_FILES = ('apps/monitor/models.py', 'apps/monitor/ssl/__init__.py', 'apps/monitor/ssl/models.py')
UI_FILES = (
    'src/routes.js', 'src/pages/home/Todo.js', 'src/layout/Notification.js',
    *('src/pages/ssl-monitor/' + name for name in (
        'index.js', 'index.module.less', 'helpers.js', 'Status.js', 'SourceSettings.js',
        'CertificateDetail.js', 'Renewal.js', 'Guidance.js', 'Reminders.js', 'ReminderEntry.js')),
)
API_TESTS = ('apps/monitor/ssl/tests.py', 'apps/monitor/ssl/test_collectors.py', 'spug/test_urls.py')


class ManifestFailure(Exception):
    pass


def validate_changes(names):
    api = {name[len('spug_api/'):] for name in names if name.startswith('spug_api/')}
    api -= set(API_TESTS)
    ui = {name[len('spug_web/'):] for name in names if name.startswith('spug_web/')
          and not name.endswith('.test.js')}
    if api != set(API_FILES) or ui != set(UI_FILES):
        raise ManifestFailure('ssl-runtime-file-set-not-exact')
