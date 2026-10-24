import json
from libs.mixins import AdminView
from libs.utils import json_response
from . import health
from .health_probe import STARTABLE


class PlatformHealthView(AdminView):
    def get(self, request):
        return json_response(health.snapshot())

    def post(self, request):
        try:
            if len(request.body) > 1024:
                raise ValueError('oversize')
            data = json.loads(request.body)
            if (not isinstance(data, dict) or set(data) != {'action', 'service'}
                    or data['action'] != 'start' or not isinstance(data['service'], str)
                    or data['service'] not in STARTABLE):
                raise ValueError('invalid action')
        except (ValueError, TypeError, UnicodeError):
            return json_response(error='操作参数错误，仅支持启动指定的已停止服务。')
        return json_response(health.recover_service(data['service'], request.user.pk))
