"""The first-batch menu patch must meet the complete UI-only release contract."""
from test_branding_cleanup_release import BrandingCleanupReleaseTests as _Base


class HideFirstBatchReleaseTests(_Base):
    profile = 'hide-first-batch-ui'
    ui = {
        'spug_web/src/routes.js',
        'spug_web/src/layout/Sider.js',
        'spug_web/src/pages/system/setting/index.js',
        'spug_web/src/pages/exec/panel/index.js',
    }
    ui_count = 4


del _Base
