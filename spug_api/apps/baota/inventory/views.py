"""Fail-closed response boundary, including crypto and unexpected ORM failures."""
from django.views import View

from libs import json_response
from apps.baota.validation import parse_body
from . import service


class InventoryView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionError:
            return json_response(error='仅超级管理员可访问资产检查台')
        except ValueError as error:
            return json_response(error=str(error))
        except Exception:
            return json_response(error='资产请求未完成，请刷新后核对状态')


class OptionsView(InventoryView):
    def get(self, request):
        service.strict(request.GET, ())
        return json_response(service.options(request.user))


class ListView(InventoryView):
    def get(self, request, kind):
        return json_response(service.listing(request.user, kind, request.GET))


class CollectView(InventoryView):
    def post(self, request):
        if len(request.body) > 2048:
            raise ValueError('请求数据超出单次限制')
        return json_response(service.collect(request.user, parse_body(request)))
