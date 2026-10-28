# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
"""Restricted global appearance settings, independent of generic settings writes."""
import json
import re
import secrets

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.views import View

from apps.setting.models import Setting
from libs import json_response


SETTING_KEY = 'ui_appearance'
PRESETS = frozenset(('classic-blue', 'jade', 'indigo', 'graphite', 'night'))
DEFAULT = {'version': 1, 'preset': 'classic-blue', 'revision': 'default'}
REVISION = re.compile(r'[0-9a-f]{32}')
CONFLICT = '外观配置已发生变化，请重新读取后确认并应用。'
UNAVAILABLE = '外观配置暂时无法读取或保存，请稍后重试。'


def _response(data='', error='', status=200):
    response = json_response(data, error)
    response.status_code = status
    response['Cache-Control'] = 'no-store'
    return response


def _appearance(setting):
    """Return only validated public fields, even for corrupt or older records."""
    try:
        value = json.loads(setting.value) if setting else None
    except (TypeError, ValueError):
        value = None
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or not isinstance(value.get('preset'), str)
            or value['preset'] not in PRESETS or not isinstance(value.get('revision'), str)
            or not REVISION.fullmatch(value['revision'])):
        return dict(DEFAULT)
    return {key: value[key] for key in DEFAULT}


class PublicAppearanceView(View):
    # Do not permit HEAD/OPTIONS or Django's automatic HEAD alias on this path.
    http_method_names = ['get']

    def get(self, request):
        try:
            setting = Setting.objects.filter(key=SETTING_KEY).first()
        except DatabaseError:
            return _response(error=UNAVAILABLE, status=503)
        return _response(_appearance(setting))


class AppearanceView(PublicAppearanceView):
    http_method_names = ['get', 'post']

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if (not user or not user.is_supper or not user.is_active or user.is_deleted):
            return _response(error='仅活跃的超级管理员可以管理全站外观配色。', status=403)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        try:
            if len(request.body) > 1024:
                raise ValueError
            value = json.loads(request.body)
        except (TypeError, ValueError, UnicodeDecodeError):
            return _response(error='请提交有效的外观配色 JSON 参数。', status=400)
        if (not isinstance(value, dict) or set(value) != {'preset', 'revision'}
                or not isinstance(value['preset'], str) or value['preset'] not in PRESETS
                or not isinstance(value['revision'], str)
                or not (value['revision'] == 'default' or REVISION.fullmatch(value['revision']))):
            return _response(error='请选择有效的配色模板，并携带读取到的 revision。', status=400)

        try:
            setting = Setting.objects.filter(key=SETTING_KEY).first()
            if value['revision'] != _appearance(setting)['revision']:
                return _response(error=CONFLICT, status=409)
            saved = {'version': 1, 'preset': value['preset'], 'revision': secrets.token_hex(16)}
            raw = json.dumps(saved)
            with transaction.atomic():
                if setting is None:
                    # The unique key also protects concurrent first-time saves.
                    Setting.objects.create(key=SETTING_KEY, value=raw)
                elif not Setting.objects.filter(pk=setting.pk, value=setting.value).update(value=raw):
                    return _response(error=CONFLICT, status=409)
        except IntegrityError:
            return _response(error=CONFLICT, status=409)
        except OperationalError as error:
            # SQLite locks / MySQL deadlocks are retryable concurrent writes.
            if 'locked' in str(error).lower() or (error.args and error.args[0] in (1205, 1213)):
                return _response(error=CONFLICT, status=409)
            return _response(error=UNAVAILABLE, status=503)
        except DatabaseError:
            return _response(error=UNAVAILABLE, status=503)
        return _response(saved)
