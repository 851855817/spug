"""Upload endpoints authorize before accessing JSON or multipart bodies."""
from django.core.files.uploadhandler import TemporaryFileUploadHandler

from libs import json_response
from .task_views import TaskBaseView
from .upload_store import (limits, create_bundle, owned_bundle, receive_file,
                           seal_bundle, discard_bundle, bundle_view)


def _permission(user):
    from .task_service import permission
    permission(user, 'create')


class UploadBundlesView(TaskBaseView):
    def post(self, request):
        _permission(request.user)
        data = self.body(request)
        if set(data) != {'files'}:
            raise ValueError('上传清单格式无效')
        return json_response(bundle_view(create_bundle(request.user, data['files'])))


class UploadFileView(TaskBaseView):
    def post(self, request, bundle_id, file_id):
        bundle = owned_bundle(request.user, bundle_id)
        if bundle.state != 'draft':
            raise ValueError('文件包已封存，不能修改')
        if not bundle.files.filter(pk=file_id, state='pending').exists():
            raise ValueError('上传文件不存在、正在接收或已经完成')
        try:
            length = int(request.META.get('CONTENT_LENGTH', ''))
        except (TypeError, ValueError):
            raise ValueError('上传请求长度无效') from None
        if not 0 < length <= limits()['file_bytes'] + 1024**2:
            raise ValueError('上传请求超出单次限制')
        # Replace the default memory handler before request.POST/FILES parsing.
        request.upload_handlers = [TemporaryFileUploadHandler(request)]
        files = request.FILES
        if set(files) != {'file'} or len(files.getlist('file')) != 1 or request.POST:
            raise ValueError('请提交单个 file 上传字段')
        source = receive_file(request.user, bundle_id, file_id, files['file'])
        return json_response(dict(id=source.pk, path=source.relative_path, size=source.size, state=source.state))


class UploadSealView(TaskBaseView):
    def post(self, request, bundle_id):
        owned_bundle(request.user, bundle_id)
        if self.body(request):
            raise ValueError('封存请求参数无效')
        return json_response(bundle_view(seal_bundle(request.user, bundle_id)))


class UploadDiscardView(TaskBaseView):
    def post(self, request, bundle_id):
        owned_bundle(request.user, bundle_id)
        if self.body(request):
            raise ValueError('释放请求参数无效')
        return json_response(bundle_view(discard_bundle(request.user, bundle_id)))
