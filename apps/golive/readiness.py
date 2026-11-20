"""Is the system set up for real use? Each check returns (ok, label, detail)."""

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Role, TOTPDevice, User
from apps.billing.models import ServiceItem
from apps.pharmacy_rx.models import Drug
from apps.stock.models import StockBatch
from apps.wards.models import Bed

from .models import Issue

DEMO_USERNAMES = {r.value for r in Role}  # what seed_demo creates: one account named after each role


def _names(items, limit=6):
    items = list(items)
    shown = ", ".join(str(i) for i in items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def run():
    results = []

    def add(ok, label, detail=""):
        results.append((bool(ok), label, detail))

    add("[" not in settings.HOSPITAL_NAME, "Hospital name is set", settings.HOSPITAL_NAME)
    add(
        settings.HMS_MODE == "local" or settings.HMS_MODE == "training",
        "Running as a hospital or training server",
        settings.HMS_MODE,
    )
    if settings.HMS_MODE == "local":
        add(not settings.DEBUG, "Debug mode is off", "")
        add(settings.TWO_FACTOR_REQUIRED, "Two-factor sign-in is required for administrators", "")

    active = User.objects.filter(is_active=True)
    missing = [label for value, label in Role.choices if not active.filter(role=value).exists()]
    add(not missing, "Every role has at least one active account", "Missing: " + _names(missing) if missing else "")
    demo = active.filter(username__in=DEMO_USERNAMES)
    add(not demo.exists(), "No demo accounts are left", _names(u.username for u in demo))
    admins = active.filter(role=Role.ADMIN) | active.filter(is_superuser=True)
    without_2fa = [
        u.username
        for u in admins.distinct()
        if not TOTPDevice.objects.filter(user=u, confirmed_at__isnull=False).exists()
    ]
    add(
        not without_2fa or not settings.TWO_FACTOR_REQUIRED,
        "Every administrator has enrolled an authenticator app",
        _names(without_2fa),
    )
    waiting = active.filter(must_change_password=True)
    add(
        True,
        "Accounts still on a temporary password",
        f"{waiting.count()}: {_names(u.username for u in waiting)}" if waiting else "None",
    )

    unpriced = [
        s for s in ServiceItem.objects.filter(is_active=True) if not s.price_for("cash") or s.price_for("cash") <= 0
    ]
    add(not unpriced, "Every active service has a self-pay price above zero", _names(s.code for s in unpriced))
    add(ServiceItem.objects.filter(code="REG", is_active=True).exists(), "A registration fee exists (code REG)", "")

    today = timezone.localdate()
    no_stock = []
    for drug in Drug.objects.filter(is_active=True):
        qty = (
            StockBatch.objects.filter(drug=drug, expiry_date__gte=today).aggregate(t=Sum("movements__quantity"))["t"]
            or 0
        )
        if qty <= 0:
            no_stock.append(drug)
    add(
        not no_stock,
        "Every active drug has counted, unexpired opening stock",
        _names(no_stock) if no_stock else "",
    )
    add(Bed.objects.exists(), "Wards and beds are entered", f"{Bed.objects.count()} beds")
    add(active.filter(role=Role.DOCTOR).exists(), "At least one doctor is set up", "")

    from apps.ops.checks import run_all

    if settings.HMS_MODE == "local":
        bad = [name for name, (ok, _msg) in run_all().items() if not ok]
        add(not bad, "Health checks pass (database, disk, backup, sync)", _names(bad))

    critical = Issue.objects.filter(
        severity=Issue.Severity.CRITICAL, status__in=[Issue.Status.OPEN, Issue.Status.WORKAROUND]
    )
    add(not critical.exists(), "No critical issue is open", f"{critical.count()} open" if critical else "")
    return results


def ready(results=None):
    return all(ok for ok, _label, _detail in (results or run()))
