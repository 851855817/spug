import json

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.views import View

from apps.host.models import Group, Host
from apps.account import access
from libs import json_response
from . import catalogue, service
from .models import OpsBaseline, OpsRun


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


class OpsView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            service.require_admin(getattr(request, 'user', None))
            return super().dispatch(request, *args, **kwargs)
        except ValueError as exc:
            return json_response(error=str(exc))
        except ObjectDoesNotExist:
            return json_response(error='作业、主机或基线不存在，请刷新后重试')
        except DatabaseError:
            return json_response(error='作业状态正在更新，请刷新后重试')
        except Exception:
            # Global middleware exposes exception text; keep this namespace safe.
            return json_response(error='运维作业处理失败，请刷新后重试')


class CatalogView(OpsView):
    def get(self, request):
        return json_response({'jobs': catalogue.jobs(),
                              'hosts': [service.host_view(x, request.user) for x in access.scope(request.user, 'host').prefetch_related('groups').order_by('id')],
                              'groups': list(Group.objects.filter(pk__in=access.group_ids(request.user, 'host')).order_by('id').values('id', 'name', 'parent_id'))})


class RunsView(OpsView):
    def get(self, request):
        try:
            limit = min(100, max(1, int(request.GET.get('page_size', request.GET.get('limit', 30)))))
            page = max(1, int(request.GET.get('page', 1)))
            offset = max(0, int(request.GET.get('offset', (page - 1) * limit)))
        except (ValueError, TypeError):
            raise ValueError('分页参数无效') from None
        runs = service.visible_runs(request.user).prefetch_related('targets')
        if request.GET.get('job'):
            runs = runs.filter(job=request.GET['job'])
        if request.GET.get('state'):
            runs = runs.filter(state=request.GET['state'])
        return json_response({'items': [service.run_view(x, detail=False, user=request.user) for x in runs[offset:offset + limit]],
                              'total': runs.count()})

    def post(self, request):
        return json_response(service.run_view(service.create_run(request.user, body(request)), user=request.user))


class RunView(OpsView):
    def get(self, request, run_id):
        run = service.authorized_run(request.user, OpsRun.objects.get(pk=run_id))
        return json_response(service.run_view(run, user=request.user))


class ContinueView(OpsView):
    def post(self, request, run_id):
        return json_response(service.run_view(service.continue_run(request.user, run_id, body(request)), user=request.user))


class CancelView(OpsView):
    def post(self, request, run_id):
        return json_response(service.run_view(service.cancel_run(request.user, run_id), user=request.user))


class BaselinesView(OpsView):
    def get(self, request):
        try:
            page = max(1, int(request.GET.get('page', 1)))
            page_size = min(100, max(1, int(request.GET.get('page_size', 30))))
        except (TypeError, ValueError):
            raise ValueError('分页参数无效') from None
        baselines = service.visible_baselines(request.user).select_related('target')
        for field in ('job', 'version'):
            if request.GET.get(field):
                baselines = baselines.filter(**{field: request.GET[field]})
        if request.GET.get('php_version'):
            baselines = baselines.filter(params__php_version=request.GET['php_version'])
        query = request.GET.get('q', '').strip()
        if query:
            baselines = baselines.filter(name__icontains=query[:100])
        offset = (page - 1) * page_size
        return json_response({'items': [service.baseline_view(x, user=request.user) for x in baselines[offset:offset + page_size]],
                              'total': baselines.count()})

    def post(self, request):
        return json_response(service.baseline_view(service.create_baseline(request.user, body(request)), user=request.user))


class DiffView(OpsView):
    def get(self, request, run_id):
        try:
            baseline_id = int(request.GET.get('baseline_id', ''))
        except (TypeError, ValueError):
            raise ValueError('请选择基线') from None
        run = service.authorized_run(request.user, OpsRun.objects.get(pk=run_id))
        if not service.visible_baselines(request.user).filter(pk=baseline_id).exists():
            raise ValueError('基线不存在或无权访问')
        return json_response(service.diff_run(run, baseline_id, user=request.user))
