"""A login-only UI change preserves API bytes, authentication and business data."""
from test_branding_cleanup_release import BrandingCleanupReleaseTests as _Base


class LoginWarningReleaseTests(_Base):
    profile = 'login-warning-ui'
    ui = {'spug_web/src/pages/login/index.js'}
    ui_count = 1


del _Base
