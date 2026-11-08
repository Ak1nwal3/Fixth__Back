from django.urls import path

from common.views import DocumentDownloadView

urlpatterns = [
    path(
        "documents/<uuid:pk>/download/",
        DocumentDownloadView.as_view(),
        name="document-download",
    ),
]
