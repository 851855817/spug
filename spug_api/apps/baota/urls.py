from django.urls import include, path
from django.db import transaction
from .views import GroupView, PanelView
from .connection_views import CertificateView, ConnectionView
from .upload_views import UploadBundlesView, UploadFileView, UploadSealView, UploadDiscardView
from .task_views import (TasksView, TaskDetailView, TaskActionView, TaskOptionsView,
                         TaskResourcesView, TaskCatalogView)


@transaction.non_atomic_requests
def upload_results(request, target_id):
    # Kept lazy so storage can initialize independently of upload task execution.
    from .task_views import UploadResultsView
    return UploadResultsView.as_view()(request, target_id=target_id)


urlpatterns = [
    path('monitor/', include('apps.baota.resource_monitor.urls')),
    path('', PanelView.as_view()),
    path('group/', GroupView.as_view()),
    path('connection/', ConnectionView.as_view()),
    path('certificate/', CertificateView.as_view()),
    path('tasks/', TasksView.as_view()),
    path('tasks/<int:task_id>/', TaskDetailView.as_view()),
    *[path('tasks/<int:task_id>/' + action + '/', TaskActionView.as_view(), {'action': action})
      for action in ('start', 'continue', 'cancel', 'reconcile', 'resolve', 'retry')],
    path('task-options/', TaskOptionsView.as_view()),
    path('resources/', TaskResourcesView.as_view()),
    path('catalog/', TaskCatalogView.as_view()),
    path('uploads/', UploadBundlesView.as_view()),
    path('uploads/<str:bundle_id>/files/<str:file_id>/', UploadFileView.as_view()),
    path('uploads/<str:bundle_id>/seal/', UploadSealView.as_view()),
    path('uploads/<str:bundle_id>/discard/', UploadDiscardView.as_view()),
    path('targets/<int:target_id>/uploads/', upload_results),
]
