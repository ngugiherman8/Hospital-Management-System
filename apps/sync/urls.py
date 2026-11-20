from django.urls import path

from . import views

urlpatterns = [
    path("receive/", views.receive, name="sync_receive"),
    path("status/", views.status, name="sync_status"),
]
