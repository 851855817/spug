"""Public website-list certificate metadata, without extra panel requests."""
import json
from unittest import TestCase

from .resources import get_resource
from .test_operations import PanelFixture


class SiteSSLSummaryTests(TestCase):
    def test_certificate_summary_keeps_only_valid_expiry_and_no_extra_reads(self):
        for version in ('9.2.0', '11.7.0', '13.1.0'):
            with self.subTest(version=version):
                client = PanelFixture()
                client.info['version'] = version
                client.sites[0]['ssl'] = {'notAfter': '2027-01-20', 'endtime': 90,
                                          'private_key': 'DO_NOT_EXPOSE', 'issuer': 'unused'}
                result = get_resource(client, 'sites')
                self.assertEqual(result['sites'][0]['ssl'], {'state': 'configured', 'expires_at': '2027-01-20'})
                self.assertNotIn('DO_NOT_EXPOSE', json.dumps(result))
                self.assertEqual([call[1] for call in client.calls], ['GetSystemTotal', 'getData'])

    def test_absent_malformed_or_unsupported_ssl_does_not_claim_unconfigured(self):
        for raw, expected in [(-1, 'unconfigured'), (None, 'unknown'), (False, 'unknown'),
                              ({'notAfter': '2027-02-30'}, 'unknown'),
                              ({'notAfter': '2027-1-2'}, 'unknown'),
                              ({'notAfter': 'secret-not-a-date'}, 'unknown')]:
            with self.subTest(raw=raw):
                client = PanelFixture()
                client.sites[0]['ssl'] = raw
                summary = get_resource(client, 'sites')['sites'][0]['ssl']
                self.assertEqual(summary, {'state': expected, 'expires_at': ''})
