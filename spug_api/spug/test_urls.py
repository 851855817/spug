"""Module test routes; avoid requiring unrelated LDAP/system integrations."""
from django.urls import include, path
from apps.setting.health_views import PlatformHealthView

urlpatterns = [path('baota/', include('apps.baota.urls')), path('ops/', include('apps.ops.urls')),
               path('setting/health/', PlatformHealthView.as_view())]
