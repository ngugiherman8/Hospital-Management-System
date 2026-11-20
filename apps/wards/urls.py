from django.urls import path

from . import views

urlpatterns = [
    path("", views.board, name="ward_board"),
    path("visit/<int:pk>/admit/", views.admit_view, name="admit"),
    path("admission/<int:pk>/transfer/", views.transfer_view, name="transfer"),
    path("admission/<int:pk>/discharge/", views.discharge_view, name="discharge"),
]
