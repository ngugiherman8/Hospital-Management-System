from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from apps.appointments.models import Appointment, Status
from apps.patients.models import Patient


@login_required
def home(request):
    today = Appointment.objects.filter(visit_date=timezone.localdate())
    context = {
        "patients_total": Patient.objects.count(),
        "today_total": today.count(),
        "waiting": today.filter(status__in=[Status.WAITING_TRIAGE, Status.WAITING_DOCTOR]).count(),
        "seen": today.filter(status=Status.COMPLETED).count(),
    }
    return render(request, "home.html", context)
