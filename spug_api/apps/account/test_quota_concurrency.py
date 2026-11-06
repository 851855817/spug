"""Real independent transactions; skipped on SQLite, no customer transports."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.account import access
from apps.account.models import ResourceGrant, ResourceOwnership, User
from apps.host.models import Host
from apps.baota.models import Panel, PanelGroup
from apps.baota.resource_monitor.models import ResourceMonitorPreference as Preference


@skipUnless(connection.vendor == 'mysql', 'MariaDB transaction acceptance only')
class QuotaConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create(username='admin', is_supper=True)
        self.a = User.objects.create(username='a')
        self.b = User.objects.create(username='b')
        access.set_policy(self.admin, self.a, 1)
        access.set_policy(self.admin, self.b, 1)

    def race(self, left, right):
        barrier = threading.Barrier(2)
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                action()
                return 'ok'
            except access.AccessDenied:
                return 'quota'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, action) for action in (left, right)]
            return [future.result(timeout=15) for future in futures]

    def test_two_additions_cannot_take_last_slot(self):
        def create(name):
            with access.quota_guard([self.a.pk]):
                time.sleep(.1)
                Host.objects.create(name=name, hostname='192.0.2.1', username='root', created_by_id=self.a.pk)
        self.assertCountEqual(self.race(lambda: create('one'), lambda: create('two')), ['ok', 'quota'])
        self.assertEqual(Host.objects.filter(created_by=self.a).count(), 1)
        self.assertEqual(access.quota_usage(self.a)['used'], 1)

    def test_parallel_assignments_cannot_exceed_limit(self):
        hosts = [Host.objects.create(name=str(i), hostname='192.0.2.1', username='root', created_by=self.admin)
                 for i in range(2)]
        def grant(host):
            access.assign(self.admin, self.a, 'host', [host.pk], 'manage')
        self.assertCountEqual(self.race(lambda: grant(hosts[0]), lambda: grant(hosts[1])), ['ok', 'quota'])
        self.assertEqual(ResourceGrant.objects.filter(user=self.a).count(), 1)

    def test_unlink_and_new_grant_share_current_affected_accounts(self):
        group = PanelGroup.objects.create(name='panels')
        host = Host.objects.create(name='shared', hostname='192.0.2.1', username='root', created_by=self.admin)
        panels = [Panel.objects.create(name=str(i), url='http://192.0.2.1:8888', username='test',
                    password_ciphertext='', group=group, created_by=self.admin) for i in range(2)]
        for panel in panels:
            Preference.objects.create(panel=panel, linked_host=host)
        access.assign(self.admin, self.b, 'panel', [panels[1].pk], 'read')
        def unlink():
            with access.quota_guard(lambda: access.affected_accounts('panel', [panels[0].pk]), actor=self.admin):
                Preference.objects.filter(panel=panels[0]).update(linked_host=None)
        def grant():
            access.assign(self.admin, self.b, 'panel', [panels[0].pk], 'read')
        self.assertCountEqual(self.race(unlink, grant), ['ok', 'quota'])
        self.assertEqual(access.quota_usage(self.b)['used'], 1)

    def test_opposite_transfers_finish_without_deadlock_and_keep_creator(self):
        access.set_policy(self.admin, self.a, 2)
        access.set_policy(self.admin, self.b, 2)
        left = Host.objects.create(name='left', hostname='192.0.2.1', username='root', created_by=self.a)
        right = Host.objects.create(name='right', hostname='192.0.2.2', username='root', created_by=self.b)
        self.assertEqual(self.race(lambda: access.transfer(self.admin, 'host', left.pk, self.b),
                                   lambda: access.transfer(self.admin, 'host', right.pk, self.a)), ['ok', 'ok'])
        left.refresh_from_db()
        right.refresh_from_db()
        self.assertEqual((left.created_by_id, right.created_by_id), (self.a.pk, self.b.pk))
        self.assertEqual(access.allowed_ids(self.a, 'host'), {right.pk})
        self.assertEqual(access.allowed_ids(self.b, 'host'), {left.pk})

    def test_failed_transfer_rolls_back_ownership_and_audit(self):
        host = Host.objects.create(name='left', hostname='192.0.2.1', username='root', created_by=self.a)
        access.set_policy(self.admin, self.b, 0)
        with self.assertRaises(access.AccessDenied):
            access.transfer(self.admin, 'host', host.pk, self.b)
        self.assertEqual(access.owner_id('host', host), self.a.pk)
        self.assertFalse(ResourceOwnership.objects.filter(host=host).exists())
