from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.accounts.forms import LockoutAuthenticationForm
from apps.accounts.passwords import password_change
from apps.accounts.twofactor import otp_setup, otp_verify
from apps.core.views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html", authentication_form=LockoutAuthenticationForm
        ),
        name="login",
    ),
    path("password/", password_change, name="password_change"),
    path("2fa/setup/", otp_setup, name="otp_setup"),
    path("2fa/verify/", otp_verify, name="otp_verify"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("patients/", include("apps.patients.urls")),
    path("appointments/", include("apps.appointments.urls")),
    path("clinical/", include("apps.clinical.urls")),
    path("orders/", include("apps.orders.urls")),
    path("prescriptions/", include("apps.pharmacy_rx.urls")),
    path("wards/", include("apps.wards.urls")),
    path("imaging/", include("apps.imaging.urls")),
    path("maternity/", include("apps.maternity.urls")),
    path("dental/", include("apps.dental.urls")),
    path("billing/", include("apps.billing.urls")),
    path("stock/", include("apps.stock.urls")),
    path("hr/", include("apps.hr.urls")),
    path("sync/", include("apps.sync.urls")),
    path("ops/", include("apps.ops.urls")),
    path("golive/", include("apps.golive.urls")),
]
