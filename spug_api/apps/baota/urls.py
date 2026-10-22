from django.urls import path
from .views import GroupView, PanelView
from .connection_views import CertificateView, ConnectionView
from .task_views import (TasksView, TaskDetailView, TaskActionView, TaskOptionsView,
                         TaskResourcesView, TaskCatalogView)

urlpatterns = [
    path('', PanelView.as_view()),
    path('group/', GroupView.as_view()),
    path('connection/', ConnectionView.as_view()),
    path('certificate/', CertificateView.as_view()),
    path('tasks/', TasksView.as_view()),
    path('tasks/<int:task_id>/', TaskDetailView.as_view()),
    *[path('tasks/<int:task_id>/' + action + '/', TaskActionView.as_view(), {'action': action})
      for action in ('start', 'cancel', 'reconcile', 'resolve', 'retry')],
    path('task-options/', TaskOptionsView.as_view()),
    path('resources/', TaskResourcesView.as_view()),
    path('catalog/', TaskCatalogView.as_view()),
]
