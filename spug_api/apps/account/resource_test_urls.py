from django.urls import path
from .resource_views import ResourceView
from .views import UserView

urlpatterns = [path('resources/', ResourceView.as_view()), path('user/', UserView.as_view())]
