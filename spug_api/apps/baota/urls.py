from django.urls import path
from .views import GroupView, PanelView

urlpatterns = [
    path('', PanelView.as_view()),
    path('group/', GroupView.as_view()),
]
