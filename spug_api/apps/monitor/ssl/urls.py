from django.urls import path
from . import views

urlpatterns = [
    path('', views.ListView.as_view()),
    path('sources/', views.SourcesView.as_view()),
    path('checks/', views.CheckView.as_view()),
    path('certificates/<int:certificate_id>/visibility/', views.VisibilityView.as_view()),
    path('jobs/', views.JobsView.as_view()),
    path('jobs/<int:job_id>/', views.JobsView.as_view()),
    path('renew/', views.RenewView.as_view()),
    path('renew/<int:job_id>/start/', views.StartView.as_view()),
    path('renew/<int:job_id>/verify/', views.VerifyView.as_view()),
    path('renew/<int:job_id>/cancel/', views.CancelView.as_view()),
    path('reminders/', views.RemindersView.as_view()),
    path('reminders/<int:reminder_id>/mute/', views.MuteView.as_view()),
]
