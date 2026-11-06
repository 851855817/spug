from django.urls import include, path
from .views import BaselinesView, CancelView, CatalogView, ContinueView, DiffView, RunsView, RunView


urlpatterns = [
    path('expiry/', include('apps.ops.expiry.urls')),
    path('catalog/', CatalogView.as_view()),
    path('runs/', RunsView.as_view()),
    path('runs/<int:run_id>/', RunView.as_view()),
    path('runs/<int:run_id>/report/', RunView.as_view()),
    path('runs/<int:run_id>/continue/', ContinueView.as_view()),
    path('runs/<int:run_id>/cancel/', CancelView.as_view()),
    path('runs/<int:run_id>/diff/', DiffView.as_view()),
    path('baselines/', BaselinesView.as_view()),
]
