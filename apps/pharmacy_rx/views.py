from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.appointments.models import Appointment
from apps.clinical.views import EDITABLE, _clinic_ok
from apps.core.permissions import role_required

from .forms import PrescribeForm
from .models import Prescription, PrescriptionItem


@role_required(Role.DOCTOR, Role.DENTIST)
@require_POST
def prescribe(request, pk):
    visit = get_object_or_404(Appointment.objects.select_related("patient"), pk=pk)
    consultation = getattr(visit, "consultation", None)
    if visit.status not in EDITABLE or consultation is None or not _clinic_ok(request.user, visit):
        raise PermissionDenied
    form = PrescribeForm(request.POST, patient=visit.patient)
    if form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                prescription = consultation.prescriptions.filter(dispensing_status="pending").order_by(
                    "-id"
                ).first() or Prescription.objects.create(consultation=consultation, prescribed_by=request.user)
                acknowledged = form.needs_acknowledgement
                PrescriptionItem.objects.create(
                    prescription=prescription,
                    drug=data["drug"],
                    dose=data["dose"],
                    frequency=data["frequency"],
                    days=data["days"],
                    quantity=data["quantity"],
                    allergy_acknowledged_by=request.user if acknowledged else None,
                    allergy_acknowledged_at=timezone.now() if acknowledged else None,
                )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"{data['drug']} added to the prescription.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("visit_detail", pk=visit.pk)
