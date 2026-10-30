"""The self-use UI patch shares the complete no-API release contract."""
from test_branding_cleanup_release import BrandingCleanupReleaseTests as _Base


class SelfUseBrandingReleaseTests(_Base):
    profile = 'self-use-branding-ui'
    ui = {
        'spug_web/src/branding/LegalNotice.js',
        'spug_web/src/layout/Header.js',
        'spug_web/src/pages/ssh/index.js',
        'spug_web/src/pages/system/setting/BasicSetting.js',
    }
    ui_count = 4


del _Base
