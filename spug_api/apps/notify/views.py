# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from apps.notify.models import Notify, NotifyAudience
from apps.account import access
from libs import json_response, JsonParser, Argument


class NotifyView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            access.current_user(request.user)
            return super().dispatch(request, *args, **kwargs)
        except access.AccessDenied as error:
            return json_response(error=str(error))

    def get(self, request):
        user = access.current_user(request.user)
        if user.is_supper:
            return json_response(Notify.objects.all())
        receipts = visible_receipts(user)
        return json_response([{**row.notification.to_dict(), 'unread': row.unread} for row in receipts])

    def patch(self, request):
        form, error = JsonParser(
            Argument('ids', type=list, help='参数错误')
        ).parse(request.body)
        if error is None:
            user = access.current_user(request.user)
            if user.is_supper:
                Notify.objects.filter(id__in=form.ids).update(unread=False)
            else:
                allowed = {row.notification_id for row in visible_receipts(user)}
                if not access.identifiers(form.ids).issubset(allowed):
                    access.reject(user, '通知不存在或无权访问')
                NotifyAudience.objects.filter(user=user, notification_id__in=form.ids).update(unread=False)
        return json_response(error=error)


def visible_receipts(user):
    user, granted = access.permissions(user)
    hosts, panels = access.allowed_ids(user, 'host'), access.allowed_ids(user, 'panel')
    result = []
    for receipt in NotifyAudience.objects.filter(user=user).select_related('notification').order_by('-notification_id'):
        try:
            if ((not receipt.permission or receipt.permission in granted) and
                    access.identifiers(receipt.host_ids).issubset(hosts) and
                    access.identifiers(receipt.panel_ids).issubset(panels)):
                result.append(receipt)
        except ValueError:
            continue
    return result
