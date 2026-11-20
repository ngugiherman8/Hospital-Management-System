from apps.accounts.models import CLINICIAN_ROLES

from .models import ImagingReport


def critical_alerts(request):
    """Critical findings the signed-in clinician ordered and has not yet acknowledged."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.has_role(*CLINICIAN_ROLES):
        return {}
    reports = (
        ImagingReport.objects.filter(critical=True, acks__isnull=True, study__order_item__order__ordered_by=user)
        .select_related("study__patient", "study__item__test")
        .order_by("-id")[:5]
    )
    return {"critical_alerts": list(reports)}
