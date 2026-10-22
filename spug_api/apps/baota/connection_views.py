import re

from django.views import View

from libs import auth, human_datetime, json_response
from .client import PanelAPIError, PanelClient, inspect_certificate
from .models import Panel
from .validation import parse_body, positive_id


class ConnectionView(View):
    @auth('baota.panel.edit')
    def post(self, request):
        try:
            panel = Panel.objects.get(pk=positive_id(parse_body(request).get('id')))
            revision = panel.api_config_revision
            with PanelClient(panel) as client:
                result = client.request('/system', action='GetSystemTotal')
            if not isinstance(result, dict):
                raise PanelAPIError('面板系统信息格式错误')
            system = ' '.join(str(result.get(key, '')) for key in ('system', 'os', 'platform')).lower()
            if any(name in system for name in ('windows', 'win32', 'darwin', 'macos', 'mac os',
                                               'freebsd', 'openbsd', 'netbsd', 'solaris', 'sunos', 'aix', 'hp-ux')):
                raise PanelAPIError('当前仅支持 Linux 宝塔面板')
            version = result.get('version')
            if not isinstance(version, str) or not re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){1,3}(?:[a-zA-Z0-9._-]{0,20})', version):
                raise PanelAPIError('面板没有返回可识别的版本号')
            checked_at = human_datetime()
            # The network call stays outside a DB transaction. Never approve a concurrently edited configuration.
            updated = Panel.objects.filter(pk=panel.pk, api_config_revision=revision).update(
                api_checked_at=checked_at, api_version=version)
            if not updated:
                raise PanelAPIError('API 配置已变更，请重新测试连接')
            panel.api_checked_at, panel.api_version = checked_at, version
            return json_response(panel.to_view())
        except (ValueError, PanelAPIError) as exc:
            return json_response(error=str(exc))
        except Panel.DoesNotExist:
            return json_response(error='面板不存在，请刷新列表后重试')


class CertificateView(View):
    @auth('baota.panel.edit')
    def post(self, request):
        try:
            panel = Panel.objects.get(pk=positive_id(parse_body(request).get('id')))
            result = inspect_certificate(panel.api_base_url)
            if not Panel.objects.filter(pk=panel.pk, api_config_revision=panel.api_config_revision).exists():
                raise PanelAPIError('API 配置已变更，请重新检查证书')
            return json_response({**result, 'api_base_url': panel.api_base_url,
                                  'api_config_revision': panel.api_config_revision})
        except (ValueError, PanelAPIError) as exc:
            return json_response(error=str(exc))
        except Panel.DoesNotExist:
            return json_response(error='面板不存在，请刷新列表后重试')
