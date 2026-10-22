from django.urls import path
from .views import GroupView, PanelView
from .connection_views import CertificateView, ConnectionView

urlpatterns = [
    path('', PanelView.as_view()),
    path('group/', GroupView.as_view()),
    path('connection/', ConnectionView.as_view()),
    path('certificate/', CertificateView.as_view()),
]
