from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import include, path

from apps.appointments import views as appt_views
from apps.appointments.models import Clinic  # noqa: F401
from apps.core.auth_views import HospitalLoginView, TwoFactorView


def health(request):
    """Used by the monitored health check: database reachable and every migration applied."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    with connection.cursor() as c:
        c.execute("SELECT 1")
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    pending = [f"{m.app_label}.{m.name}" for m, _ in plan]
    body = {"status": "ok" if not pending else "migrations pending", "pending_migrations": pending}
    return JsonResponse(body, status=200 if not pending else 503)


urlpatterns = [
    path("admin/login/", HospitalLoginView.as_view()),  # the admin site uses the same lockout and second step
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("login/", HospitalLoginView.as_view(), name="login"),
    path("login/second-step/", TwoFactorView.as_view(), name="two_factor"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", appt_views.QueueView.as_view(), name="queue"),
    path("queue/walk-in/<int:patient_pk>/", appt_views.WalkInView.as_view(), name="walk_in"),
    path("queue/<int:pk>/emergency/", appt_views.FastTrackView.as_view(), name="queue_emergency"),
    path("patients/", include("apps.patients.urls")),
    path("appointments/", include("apps.appointments.urls")),
    path("clinical/", include("apps.clinical.urls")),
    path("orders/", include("apps.orders.urls")),
    path("rx/", include("apps.pharmacy_rx.urls")),
    path("wards/", include("apps.wards.urls")),
    path("billing/", include("apps.billing.urls")),
]
