from django.urls import include, path

urlpatterns = [
    path('host/', include('apps.host.urls')),
    path('exec/', include('apps.exec.urls')),
    path('file/', include('apps.file.urls')),
]
