"""A routes-only menu removal preserves every API byte and old business data."""
from test_branding_cleanup_release import BrandingCleanupReleaseTests as _Base


class HideSiteInventoryReleaseTests(_Base):
    profile = 'hide-site-inventory-ui'
    ui = {'spug_web/src/routes.js'}
    ui_count = 1


del _Base
