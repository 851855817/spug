"""Module test routes; avoid requiring unrelated LDAP/system integrations."""
from django.urls import include, path
from apps.setting.health_views import PlatformHealthView
from apps.setting.appearance import AppearanceView, PublicAppearanceView

urlpatterns = [path('baota/', include('apps.baota.urls')), path('ops/', include('apps.ops.urls')),
               path('setting/health/', PlatformHealthView.as_view()),
               path('setting/appearance/public/', PublicAppearanceView.as_view()),
               path('setting/appearance/', AppearanceView.as_view())]
