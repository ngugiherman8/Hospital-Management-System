from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import include, path

from apps.appointments import views as appt_views
from apps.appointments.models import Clinic  # noqa: F401


def health(request):
    """Used by the monitored health check (DB reachable)."""
    from django.db import connection

    with connection.cursor() as c:
        c.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", appt_views.QueueView.as_view(), name="queue"),
    path("queue/walk-in/<int:patient_pk>/", appt_views.WalkInView.as_view(), name="walk_in"),
    path("queue/<int:pk>/<str:action>/", appt_views.StageView.as_view(), name="queue_stage"),
    path("patients/", include("apps.patients.urls")),
    path("appointments/", include("apps.appointments.urls")),
]
