"""Strict local HTTP boundary with sanitized unexpected failures."""
from django.views import View

from libs import json_response
from apps.baota.validation import parse_body
from . import service, renewal


class SSLView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            service.permission(request.user)
            if len(request.body) > 8192:
                raise ValueError('请求数据超过单次限制')
            return super().dispatch(request, *args, **kwargs)
        except PermissionError:
            return json_response(error='仅活跃超级管理员可访问本人 SSL 监控任务')
        except ValueError as error:
            return json_response(error=str(error))
        except Exception:
            return json_response(error='SSL 请求未完成，请刷新后核对状态')


class SourcesView(SSLView):
    def get(self, request):
        service.strict(request.GET, ())
        return json_response(service.sources(request.user))

    def post(self, request):
        return json_response(service.configure(request.user, parse_body(request)))


class ListView(SSLView):
    def get(self, request):
        return json_response(service.listing(request.user, request.GET))


class CheckView(SSLView):
    def post(self, request):
        return json_response(service.enqueue_checks(request.user, parse_body(request)))


class JobsView(SSLView):
    def get(self, request, job_id=None):
        if job_id is not None:
            service.strict(request.GET, ())
            return json_response(service.get_job(request.user, job_id))
        return json_response(service.jobs_listing(request.user, request.GET))


class RenewView(SSLView):
    def post(self, request):
        return json_response(renewal.prepare(request.user, parse_body(request)))


class StartView(SSLView):
    def post(self, request, job_id):
        return json_response(renewal.start(request.user, job_id, parse_body(request)))


class VerifyView(SSLView):
    def post(self, request, job_id):
        return json_response(renewal.verify(request.user, job_id, parse_body(request)))


class CancelView(SSLView):
    def post(self, request, job_id):
        return json_response(renewal.cancel(request.user, job_id, parse_body(request)))


class RemindersView(SSLView):
    def get(self, request):
        return json_response(service.reminders(request.user, request.GET))


class MuteView(SSLView):
    def post(self, request, reminder_id):
        return json_response(service.mute_reminder(request.user, reminder_id, parse_body(request)))
