"""9.2's legacy upgrade protocol, isolated from the modern-panel fixture."""
import copy
from unittest import TestCase

from . import resources
from .client import PanelAPIError
from .operations.upgrade import UpgradePanel


class LegacyUpgradeFixture:
    """Models presence-of-force, ignored version, and a later cloud lookup."""
    def __init__(self):
        self.calls = []
        self.info = {'version': '9.2.0', 'system': 'Debian GNU/Linux 12',
                     'cpuNum': 2, 'memTotal': 2048}
        self.cloud = {'version': '13.1.0', 'hash': 'a' * 64, 'update_time': '1792659850'}
        self.started = False
        self.mutation_error = None

    def request(self, path, action=None, params=None, mutating=False, **kwargs):
        params = copy.deepcopy(params or {})
        self.calls.append((path, action, params, mutating))
        if path == '/system' and action == 'GetSystemTotal':
            return copy.deepcopy(self.info)
        if path == '/system' and action == 'upgrade_panel':
            # Official 9.2 ignores version and treats even force=false as a mutation.
            if 'force' in params:
                self.started = True
                if self.mutation_error:
                    raise self.mutation_error
                return {'status': True, 'msg': 'background repair started'}
            return {'local': {'version': self.info['version'], 'update_time': 1720000000},
                    'cloud': copy.deepcopy(self.cloud), 'upgrade': 1}
        raise AssertionError('Unexpected legacy protocol: ' + repr((path, action, params)))

    def finish(self):
        # The process checks cloud again after dispatch; it does not freeze our candidate.
        if self.started:
            self.info['version'] = self.cloud['version']


class Upgrade92Tests(TestCase):
    def setUp(self):
        self.op, self.client = UpgradePanel(), LegacyUpgradeFixture()

    def item(self, params=None):
        return self.op.preflight(self.client, params or {'channel': 'stable'})['items'][0]

    def mutations(self):
        return [call for call in self.client.calls if call[3]]

    def test_stable_is_a_distinct_validated_channel_not_an_exact_target(self):
        self.assertEqual(self.op.validate({'channel': 'stable'}), {'channel': 'stable'})
        for params in ({'channel': 'beta'}, {'channel': 'lts'},
                       {'channel': 'stable', 'version': '13.1.0'},
                       {'channel': 'stable', 'force': False}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.op.validate(params)

    def test_known_source_is_not_accepted_as_an_exact_upgrade_target(self):
        with self.assertRaises(ValueError):
            self.op.validate({'version': '9.2.0'})

    def test_exact_version_on_92_is_explicitly_blocked_without_upgrade_probe(self):
        item = self.item({'version': '13.1.0'})
        self.assertEqual(item['state'], 'failed')
        self.assertIn('无法指定', item['message'])
        self.assertFalse(any(call[1] == 'upgrade_panel' for call in self.client.calls))
        self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_preflight_only_checks_old_metadata_and_freezes_the_channel_policy(self):
        item = self.item()
        self.assertEqual(item['state'], 'ready')
        self.assertEqual(item['target'], {'channel': 'stable'})
        self.assertNotIn('required_acknowledgements', item)
        self.assertEqual(item['plan'], {'mode': 'legacy_stable', 'channel': 'stable',
            'checked_version': '13.1.0', 'update_time': 1792659850,
            'policy': self.op.LEGACY_POLICY, 'required_acknowledgements': ['legacy_panel_upgrade']})
        self.assertEqual(item['before']['checked_version'], '13.1.0')
        self.assertEqual(item['before']['policy'], self.op.LEGACY_POLICY)
        self.assertIn('无法冻结', item['message'])
        self.assertEqual([call for call in self.client.calls if call[1] == 'upgrade_panel'],
                         [('/system', 'upgrade_panel', {}, False)])
        self.assertEqual(self.mutations(), [])
        self.assertFalse(self.client.started)

    def test_candidate_reader_returns_only_bounded_public_metadata(self):
        self.client.cloud['untrusted_extra'] = 'REMOTE_SECRET'
        self.assertEqual(self.op.legacy_candidate(self.client),
                         {'checked_version': '13.1.0', 'update_time': 1792659850})

    def test_invalid_legacy_metadata_blocks_before_any_mutation(self):
        for patch in ({'version': '13.1.0;id'}, {'hash': 'short'},
                      {'update_time': False}, {'update_time': '1.5'},
                      {'version': '9.0.0-lts'}):
            with self.subTest(patch=patch):
                self.client = LegacyUpgradeFixture()
                self.client.cloud.update(patch)
                with self.assertRaises((PanelAPIError, ValueError)):
                    self.item()
                self.assertEqual(self.mutations(), [])

    def test_legacy_dispatch_rechecks_then_sends_force_without_fake_version(self):
        item = self.item()
        submitted = self.op.execute(self.client, item)
        self.assertEqual(submitted['state'], 'remote')
        self.assertEqual(self.mutations(), [('/system', 'upgrade_panel', {'force': 'true'}, True)])
        self.assertEqual(submitted['remote']['channel'], 'stable')
        self.assertNotIn('version', submitted['remote'])
        self.client.finish()
        verified = self.op.reconcile(self.client, item, submitted['remote'])
        self.assertEqual(verified['state'], 'success')
        self.assertEqual(verified['evidence']['version'], '13.1.0')
        self.assertFalse(verified['evidence']['target_version_frozen'])

    def test_candidate_change_after_preflight_requires_a_new_preflight(self):
        for patch in ({'version': '13.2.0'}, {'update_time': '1792659851'}):
            with self.subTest(patch=patch):
                self.client = LegacyUpgradeFixture()
                item = self.item()
                self.client.cloud.update(patch)
                self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_panel_version_drift_and_invalid_policy_block_dispatch(self):
        for field, value in (('panel_version', '11.7.0'), ('policy', 'unknown')):
            with self.subTest(field=field):
                self.client = LegacyUpgradeFixture()
                item = self.item()
                if field == 'policy':
                    item['plan'][field] = value
                else:
                    self.client.info['version'] = value
                self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_missing_or_modified_legacy_ack_requirement_blocks_dispatch(self):
        for value in (None, [], ['ssl_panel_sync'], ['legacy_panel_upgrade', 'unknown'],
                      'legacy_panel_upgrade'):
            with self.subTest(value=value):
                self.client = LegacyUpgradeFixture()
                item = self.item()
                if value is None:
                    item['plan'].pop('required_acknowledgements', None)
                else:
                    item['plan']['required_acknowledgements'] = value
                self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_missing_or_modified_legacy_frozen_fields_block_dispatch(self):
        for section, field, value in (
                ('plan', 'mode', None), ('plan', 'channel', 'beta'),
                ('plan', 'policy', None), ('before', 'policy', 'unknown'),
                ('plan', 'checked_version', None), ('before', 'checked_version', '11.7.0'),
                ('plan', 'update_time', None), ('before', 'update_time', 1),
                ('before', 'panel_version', None), ('target', 'channel', 'beta')):
            with self.subTest(section=section, field=field):
                self.client = LegacyUpgradeFixture()
                item = self.item()
                if value is None:
                    item[section].pop(field, None)
                else:
                    item[section][field] = value
                self.assertEqual(self.op.execute(self.client, item)['state'], 'failed')
                self.assertEqual(self.mutations(), [])

    def test_backend_cloud_change_is_observed_without_claiming_frozen_success(self):
        item = self.item()
        submitted = self.op.execute(self.client, item)
        self.client.cloud['version'] = '11.7.0'  # Also adapted, but differs from the checked candidate.
        self.client.finish()
        verified = self.op.reconcile(self.client, item, submitted['remote'])
        self.assertEqual(verified['state'], 'uncertain')
        self.assertEqual(verified['evidence']['version'], '11.7.0')
        self.assertEqual(len(self.mutations()), 1)

    def test_upgrade_to_unknown_version_can_be_read_but_never_unlocks_new_mutations(self):
        item = self.item()
        submitted = self.op.execute(self.client, item)
        self.client.info['version'] = '14.0.0'
        for _ in range(2):
            verified = self.op.reconcile(self.client, item, submitted['remote'])
            self.assertEqual(verified['state'], 'uncertain')
            self.assertEqual(verified['evidence']['version'], '14.0.0')
            self.assertFalse(verified['evidence']['version_supported'])
        with self.assertRaises(PanelAPIError):
            resources.system_info(self.client)
        self.assertEqual(len(self.mutations()), 1)

    def test_ambiguous_request_stays_read_only_and_unchanged_is_not_failure_proof(self):
        item = self.item()
        self.client.mutation_error = PanelAPIError('lost response', uncertain=True)
        submitted = self.op.execute(self.client, item)
        self.assertEqual(submitted['state'], 'uncertain')
        for _ in range(2):
            self.assertEqual(self.op.reconcile(self.client, item, submitted['remote'])['state'], 'uncertain')
        self.assertEqual(len(self.mutations()), 1)

    def test_malformed_or_non_linux_readback_is_never_success(self):
        item = self.item()
        submitted = self.op.execute(self.client, item)
        for patch in ({'version': '13.1.0;id'}, {'version': '13.1.0', 'system': 'Windows'}):
            with self.subTest(patch=patch):
                self.client.info.update(patch)
                self.assertNotEqual(self.op.reconcile(self.client, item, submitted['remote'])['state'], 'success')
        self.assertEqual(len(self.mutations()), 1)

    def test_stable_channel_is_not_used_for_modern_panels(self):
        self.client.info['version'] = '11.7.0'
        item = self.item()
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(self.mutations(), [])

    def test_stable_equal_or_older_candidate_is_not_dispatched(self):
        for version, state in (('9.2.0', 'already'), ('9.1.0', 'failed')):
            with self.subTest(version=version):
                self.client = LegacyUpgradeFixture()
                self.client.cloud['version'] = version
                item = self.item()
                self.assertEqual(item['state'], state)
                self.assertEqual(self.op.execute(self.client, item)['state'], state)
                self.assertEqual(self.mutations(), [])
