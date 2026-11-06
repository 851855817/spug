"""Fresh resource checks shared by panel entry points and HTTP workers."""
from apps.account import access


def panel_view(user, panel):
    result = panel.to_view()
    if panel.group_id not in access.group_ids(user, 'panel'):
        result['group_id'] = None
    return result


def panel_access(user, panel_id, *codes, manage=False):
    user = access.require(user, *codes)
    return access.require_resources(user, 'panel', [panel_id], manage=manage).get(pk=panel_id)


class AuthorizedClient:
    """Protect fake adapters too; the transport hook checks each real HTTP call."""
    def __init__(self, client, authorize):
        self.client, self.authorize = client, authorize

    def __getattr__(self, name):
        value = getattr(self.client, name)
        if not callable(value):
            return value
        def guarded(*args, **kwargs):
            self.authorize()
            return value(*args, **kwargs)
        return guarded
