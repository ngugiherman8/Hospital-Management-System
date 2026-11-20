from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import Role
from apps.appointments.models import Appointment, Status
from apps.billing.services import day_reconciliation
from apps.core.permissions import role_required
from apps.stock.services import stock_levels

from .checks import run_all


def health(request):
    """For monitors: 200 when every check passes, 503 otherwise. Names only, no details."""
    results = run_all()
    ok = all(r[0] for r in results.values())
    return JsonResponse({"ok": ok, "checks": {n: r[0] for n, r in results.items()}}, status=200 if ok else 503)


@role_required(Role.MANAGER)
def reports_today(request):
    today = timezone.localdate()
    visits = Appointment.objects.filter(visit_date=today)
    by_status = [(label, visits.filter(status=value).count()) for value, label in Status.choices]
    low = [r for r in stock_levels() if r["low"]]
    return render(
        request,
        "ops/reports_today.html",
        {
            "today": today,
            "total": visits.count(),
            "by_status": [row for row in by_status if row[1]],
            "money": day_reconciliation(today),
            "low": low,
            "mode": settings.HMS_MODE,
        },
    )
