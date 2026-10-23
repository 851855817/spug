from django.db import transaction
from django.urls import path

from .views import SourcesView, SettingsView, LinkView, CardsView, SampleView


urlpatterns = [
    path('sources/', SourcesView.as_view()),
    path('settings/', SettingsView.as_view()),
    path('link/', LinkView.as_view()),
    path('cards/', CardsView.as_view()),
    # Must decorate the resolved callable: production enables ATOMIC_REQUESTS.
    path('sample/', transaction.non_atomic_requests(SampleView.as_view())),
]
