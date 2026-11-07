from django.urls import path

from . import views

app_name = "appointments"
urlpatterns = [
    path("", views.AppointmentListView.as_view(), name="list"),
    path("new/<int:patient_pk>/", views.AppointmentCreateView.as_view(), name="create"),
    path("<int:pk>/<str:action>/", views.appointment_status, name="status"),
]
