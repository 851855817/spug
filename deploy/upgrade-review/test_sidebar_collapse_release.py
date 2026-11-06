"""Single Sider file overlay must meet the complete UI-only release contract."""
from test_branding_cleanup_release import BrandingCleanupReleaseTests as _Base


class SidebarCollapseReleaseTests(_Base):
    profile = 'sidebar-collapse-ui'
    ui = {'spug_web/src/layout/Sider.js'}
    ui_count = 1


del _Base
