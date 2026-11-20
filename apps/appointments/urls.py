from django.urls import path

from . import views

urlpatterns = [
    path("", views.appointment_list, name="appointment_list"),
    path("new/<int:patient_pk>/", views.appointment_create, name="appointment_create"),
    path("<int:pk>/status/", views.appointment_status, name="appointment_status"),
    path("<int:pk>/fast-track/", views.appointment_fast_track, name="appointment_fast_track"),
]
