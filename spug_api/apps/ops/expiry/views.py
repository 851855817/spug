import json

from django.views import View

from libs import json_response
from . import service, reminders


def body(request):
    if len(request.body) > 16 * 1024:
        raise ValueError('请求内容过长')
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError('请求内容必须为有效 JSON 对象') from None
    if not isinstance(data, dict):
        raise ValueError('请求内容必须为 JSON 对象')
    return data


class ExpiryView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            service.require_admin(getattr(request, 'user', None))
            return super().dispatch(request, *args, **kwargs)
        except PermissionError:
            return json_response(error='仅活跃超级管理员可访问到期管理')
        except ValueError as error:
            return json_response(error=str(error))
        except Exception:
            return json_response(error='到期管理请求未完成，请刷新后核对状态')


class OptionsView(ExpiryView):
    def get(self, request):
        service.strict(request.GET, ())
        return json_response(service.options(request.user))


class AssetsView(ExpiryView):
    def get(self, request):
        return json_response(service.listing(request.user, request.GET))


class AssetView(ExpiryView):
    def post(self, request, key):
        service.strict(request.GET, ())
        return json_response(service.save(request.user, key, body(request)))


class RenewView(ExpiryView):
    def post(self, request, key):
        service.strict(request.GET, ())
        return json_response(service.save(request.user, key, body(request), renew=True))


class HistoryView(ExpiryView):
    def get(self, request, key):
        return json_response(service.history(request.user, key, request.GET))


class RemindersView(ExpiryView):
    def get(self, request):
        return json_response(reminders.listing(request.user, request.GET))


class ReadView(ExpiryView):
    def post(self, request):
        service.strict(request.GET, ())
        return json_response(reminders.mark_read(request.user, body(request)))
