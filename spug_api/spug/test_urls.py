"""Module test routes; avoid requiring unrelated LDAP/system integrations."""
from django.urls import include, path

urlpatterns = [path('baota/', include('apps.baota.urls'))]
