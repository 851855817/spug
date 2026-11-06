from django.urls import path
from django.db import transaction

from .views import OptionsView, AssetsView, AssetView, RenewView, HistoryView, RemindersView, ReadView


urlpatterns = [
    path('options/', transaction.non_atomic_requests(OptionsView.as_view())),
    path('assets/', transaction.non_atomic_requests(AssetsView.as_view())),
    path('assets/<str:key>/', transaction.non_atomic_requests(AssetView.as_view())),
    path('assets/<str:key>/renew/', transaction.non_atomic_requests(RenewView.as_view())),
    path('assets/<str:key>/history/', transaction.non_atomic_requests(HistoryView.as_view())),
    path('reminders/', transaction.non_atomic_requests(RemindersView.as_view())),
    path('reminders/read/', transaction.non_atomic_requests(ReadView.as_view())),
]
