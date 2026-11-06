# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.urls import include, path

from .views import *

urlpatterns = [
    path('ssl/', include('apps.monitor.ssl.urls')),
    path('', DetectionView.as_view()),
    path('overview/', get_overview),
    path('test/', run_test),
]
