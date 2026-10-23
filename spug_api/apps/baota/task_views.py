"""Task UI endpoints expose selected metadata, never panel login credentials."""
import json
from functools import lru_cache
from pathlib import Path

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views import View

from libs import json_response
from .client import PanelClient
from .models import Panel, PanelGroup, PanelTaskTarget
from .operations import operation_descriptors
from .resources import get_resource
from .validation import parse_body, positive_id
from . import task_service as s


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class TaskBaseView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionError:
            return json_response(error='权限拒绝或任务不存在')
        except ValueError as exc:
            return json_response(error=str(exc))
        except Exception:
            # The global exception middleware renders exception strings. Keep all
            # unexpected ORM/transport/crypto details inside this boundary.
            return json_response(error='面板任务请求未完成，请刷新后核对状态')

    @staticmethod
    def body(request):
        if len(request.body) > s.MAX_JSON_BYTES:
            raise ValueError('请求数据超出单次限制')
        return s.bounded(parse_body(request))


class TasksView(TaskBaseView):
    def get(self, request):
        s.permission(request.user, 'view')
        s.strict(request.GET, ('page', 'page_size', 'state', 'operation'))
        page = positive_id(request.GET.get('page', 1))
        size = positive_id(request.GET.get('page_size', 20))
        if page > 100000 or size > 100:
            raise ValueError('分页范围无效')
        qs = s.visible(request.user)
        for name in ('state', 'operation'):
            value = request.GET.get(name)
            if value:
                if len(value) > 40:
                    raise ValueError('筛选条件无效')
                qs = qs.filter(**{name: value})
        return json_response({'total': qs.count(), 'page': page, 'page_size': size,
                              'items': [s.task_view(t, detail=False) for t in qs[(page - 1) * size:page * size]]})

    def post(self, request):
        task = s.prepare(request.user, self.body(request))
        return json_response(s.task_view(task))


class TaskDetailView(TaskBaseView):
    def get(self, request, task_id):
        return json_response(s.task_view(s.owned(request.user, task_id, 'view')))


class TaskActionView(TaskBaseView):
    def post(self, request, task_id, action):
        data = self.body(request)
        if action == 'start':
            task = s.start(request.user, task_id, data)
        elif action == 'resolve':
            task = s.resolve(request.user, task_id, data)
        else:
            s.strict(data, ())
            task = {'cancel': s.cancel, 'reconcile': s.reconcile, 'retry': s.retry}[action](request.user, task_id)
        return json_response(s.task_view(task))


def read_permission(user):
    if not user.has_perms(['exec.panel.create', 'exec.panel.view']):
        raise PermissionError('权限拒绝')


class TaskOptionsView(TaskBaseView):
    def get(self, request):
        read_permission(request.user)
        from .upload_store import limits
        return json_response({'panels': [s.safe_panel(p) for p in Panel.objects.select_related('group').order_by('id')],
                              'groups': [g.to_view() for g in PanelGroup.objects.all()],
                              'operations': operation_descriptors(),
                              'limits': {'panels': s.MAX_PANELS, 'children': s.MAX_CHILDREN, 'parallel': 2},
                              'upload_limits': limits(),
                              'acknowledgements': {
                                  'ssl_panel_sync': '宝塔会自动同步同域名证书至面板内其他符合条件的位置（包括未选网站、孤立副本和面板 TLS），并可能重载 Web 服务；续签还可能临时调整强制 HTTPS。冻结清单不完整。',
                                  'legacy_panel_upgrade': '确认按宝塔官方稳定通道升级：9.2 接口由后台决定实际版本，无法锁定预检候选；升级期间面板会暂时断开，结果需重新连接核实。',
                                  'php_update_restart': '确认在当前 PHP 分支内执行官方更新：补丁由在线官方脚本决定，预检目标是预计值；相关 PHP 服务可能重启，使用此分支的网站可能暂时中断，结果需核实。',
                                  'upload_overwrite': '确认覆盖最终目标目录内本次清单的同名文件；无法自动回滚，上传期间文件可能短暂不完整。'}})


class UploadResultsView(TaskBaseView):
    def get(self, request, target_id):
        s.strict(request.GET, ('page', 'page_size'))
        page, size = positive_id(request.GET.get('page', 1)), positive_id(request.GET.get('page_size', 20))
        if page > 100000 or size > 100:
            raise ValueError('分页范围无效')
        target = PanelTaskTarget.objects.get(pk=positive_id(target_id))
        s.owned(request.user, target.task_id, 'view')
        if target.task.operation != 'file_upload':
            raise ValueError('该任务不是上传任务')
        rows = target.upload_files.select_related('source').order_by('source_id')
        return json_response(s.safe_tree({'total': rows.count(), 'page': page, 'page_size': size,
            'items': [{'id': row.pk, 'source_id': row.source_id, 'relative_path': row.source.relative_path,
                'size': row.source.size, 'sha256': row.source.sha256, 'state': row.state,
                'message': row.message, 'before': row.before, 'result': row.result}
                for row in rows[(page-1)*size:page*size]]}))


class TaskResourcesView(TaskBaseView):
    def get(self, request):
        read_permission(request.user)
        s.strict(request.GET, ('panel_id', 'kind', 'site_id', 'php_version'), ('panel_id', 'kind'))
        panel = Panel.objects.get(pk=positive_id(request.GET['panel_id']))
        s.configured(panel)
        revision = panel.api_config_revision
        # TaskBaseView is non_atomic even with production ATOMIC_REQUESTS=True.
        site_id = positive_id(request.GET['site_id']) if 'site_id' in request.GET else None
        try:
            with PanelClient(panel) as client:
                result = get_resource(client, request.GET['kind'], site_id=site_id,
                                      php_version=request.GET.get('php_version'))
        except Exception:
            raise ValueError('资源读取失败，请核对 API 配置、资源类型及选择参数') from None
        if not Panel.objects.filter(pk=panel.pk, api_config_revision=revision).exists():
            raise ValueError('资源读取期间 API 配置变化，请刷新')
        return json_response(s.safe_tree(s.bounded(result, maximum=4 * s.MAX_JSON_BYTES)))


@lru_cache(maxsize=1)
def official_catalog():
    with (Path(__file__).parent / 'data' / 'official_api_catalog.json').open(encoding='utf-8') as stream:
        rows = json.load(stream)
    fields = ('分类', '操作名称或说明', '方法', '请求路径', 'action', '文档操作标识', '文档版本', '接入状态', '官方来源')
    return [{key: row.get(key, '') for key in fields} for row in rows]


class TaskCatalogView(TaskBaseView):
    def get(self, request):
        read_permission(request.user)
        s.strict(request.GET, ('q', 'category', 'page', 'page_size'))
        query, category = request.GET.get('q', '').strip(), request.GET.get('category', '').strip()
        if len(query) > 100 or len(category) > 100:
            raise ValueError('搜索条件过长')
        all_rows = official_catalog()
        rows = [r for r in all_rows if (not category or r['分类'] == category)
                and (not query or query.lower() in json.dumps(r, ensure_ascii=False).lower())]
        page, size = positive_id(request.GET.get('page', 1)), positive_id(request.GET.get('page_size', 50))
        if page > 100000 or size > 100:
            raise ValueError('分页范围无效')
        return json_response({'total': len(rows), 'catalog_total': len(all_rows), 'page': page, 'page_size': size,
                              'categories': sorted({r['分类'] for r in all_rows}),
                              'items': rows[(page - 1) * size:page * size], 'read_only': True})
