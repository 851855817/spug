from django.urls import include, path

urlpatterns = [path('baota/', include('apps.baota.urls')), path('schedule/', include('apps.schedule.urls'))]
