from django.db import transaction
from django.urls import path

from .views import OptionsView, ListView, CollectView


urlpatterns = [
    path('options/', transaction.non_atomic_requests(OptionsView.as_view())),
    path('sites/', transaction.non_atomic_requests(ListView.as_view()), {'kind': 'sites'}),
    path('backups/', transaction.non_atomic_requests(ListView.as_view()), {'kind': 'backups'}),
    path('collect/', transaction.non_atomic_requests(CollectView.as_view())),
]
