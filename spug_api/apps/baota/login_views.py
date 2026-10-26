"""Issue official, single-use administrator login tickets; never expose passwords."""
import re
import time

from django.views import View

from apps.account.models import User
from libs import json_response
from .client import PanelAPIError, PanelClient
from .models import Panel
from .validation import parse_body, positive_id


def may_login(user_id, access_token):
    # Deliberately bypass the page permission cache for this administrator capability.
    user = User.objects.filter(pk=user_id, access_token=access_token, is_active=True,
                               is_deleted=False, token_expired__gt=time.time()).first()
    if not user:
        return False
    if user.is_supper:
        return True
    permissions = set()
    for role in user.roles.all():
        values = role.get_page_perms().get('baota', {}).get('panel', [])
        if isinstance(values, list):
            permissions.update(value for value in values if isinstance(value, str))
    return {'view', 'login'} <= permissions


def configuration(panel):
    return {name: getattr(panel, name) for name in (
        'url', 'api_base_url', 'api_key_ciphertext', 'tls_ca', 'tls_fingerprint', 'api_config_revision')}


class LoginView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        response['Pragma'] = 'no-cache'
        response['Referrer-Policy'] = 'no-referrer'
        return response

    def post(self, request):
        user_id, access_token = request.user.pk, request.user.access_token
        try:
            if not may_login(user_id, access_token):
                return json_response(error='权限拒绝：需要查看面板和一键登录权限')
            panel = Panel.objects.get(pk=positive_id(parse_body(request).get('id')))
            original = configuration(panel)
            if not panel.api_base_url or not panel.api_key_ciphertext:
                return json_response(error='请先编辑此面板并配置 API 密钥，保存的账户密码不能生成免登录票据')
            with PanelClient(panel) as client:
                result = client.request('/config', action='get_tmp_token', mutating=True)
            if (not isinstance(result, dict) or result.get('status') is not True or
                    not isinstance(result.get('msg'), str) or
                    re.fullmatch(r'[A-Za-z0-9_]{64}', result['msg']) is None):
                return json_response(error='宝塔未返回有效登录票据，请确认面板支持官方临时登录 API')
            current = Panel.objects.filter(pk=panel.pk).first()
            if current is None or configuration(current) != original:
                return json_response(error='面板配置已变更，请刷新列表后重试')
            if not may_login(user_id, access_token):
                return json_response(error='登录权限或会话已变更，请重新登录 Spug')
            return json_response({'url': panel.api_base_url.rstrip('/') + '/login?tmp_token=' + result['msg']})
        except Panel.DoesNotExist:
            return json_response(error='面板不存在，请刷新列表后重试')
        except (ValueError, PanelAPIError) as exc:
            return json_response(error=str(exc))
        except Exception:
            # Do not let the generic exception middleware reflect token-bearing objects.
            return json_response(error='获取登录票据失败，请稍后重试')
