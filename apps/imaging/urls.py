from django.urls import path

from . import views

urlpatterns = [
    path("", views.worklist, name="imaging_worklist"),
    path("study/<int:pk>/", views.study_detail, name="study_detail"),
    path("study/<int:pk>/action/", views.study_action, name="study_action"),
    path("study/<int:pk>/report/", views.report_save, name="report_save"),
    path("study/<int:pk>/file/", views.file_upload, name="file_upload"),
    path("file/<int:pk>/", views.file_download, name="file_download"),
    path("report/<int:pk>/acknowledge/", views.critical_acknowledge, name="critical_acknowledge"),
    path("visit/<int:pk>/order/", views.imaging_order, name="imaging_order"),
]
