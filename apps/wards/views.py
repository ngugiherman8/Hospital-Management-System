from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.appointments.models import Appointment, TransitionBlocked
from apps.billing.services import clearance_status
from apps.core.audit import log_access
from apps.core.permissions import role_required

from .forms import AdmitForm, DischargeForm, TransferForm
from .models import Admission, Bed, BedUnavailable, Ward
from .models import admit as admit_patient
from .models import discharge as discharge_patient
from .models import transfer as transfer_patient

BOARD_ROLES = (Role.NURSE, Role.DOCTOR, Role.RECEPTION)  # administrators always pass
DOCTOR = (Role.DOCTOR,)
NURSE_OR_DOCTOR = (Role.NURSE, Role.DOCTOR)

# Things that can go wrong in a ward action and should be shown to the person, not crash the page.
EXPECTED_ERRORS = (BedUnavailable, TransitionBlocked, PermissionError)


def _visit_back(visit):
    return redirect("visit_detail", pk=visit.pk)


@role_required(*BOARD_ROLES)
def board(request):
    """Every ward with its beds, and who is in each occupied bed."""
    active = Admission.objects.filter(discharged_at__isnull=True).select_related("patient")
    wards = Ward.objects.prefetch_related(
        Prefetch("beds", queryset=Bed.objects.prefetch_related(Prefetch("admissions", queryset=active, to_attr="active_admissions")))
    )
    rows = []
    for ward in wards:
        beds = []
        for bed in ward.beds.all():
            adm = bed.active_admissions[0] if bed.active_admissions else None
            beds.append((bed, adm))
        rows.append((ward, beds))

    all_beds = [bed for _, beds in rows for bed, _ in beds]
    counts = {
        "free": sum(1 for b in all_beds if b.status == Bed.Status.FREE),
        "occupied": sum(1 for b in all_beds if b.status == Bed.Status.OCCUPIED),
    }
    return render(
        request,
        "wards/board.html",
        {
            "rows": rows,
            "counts": counts,
            "can_move": request.user.has_role(*NURSE_OR_DOCTOR),
            "transfer_form": TransferForm(),
        },
    )


@role_required(*DOCTOR)
def admit_view(request, pk):
    """Admit the visit's patient to a free bed."""
    visit = get_object_or_404(Appointment.objects.select_related("patient"), pk=pk)
    if getattr(visit, "admission", None) is not None:
        messages.error(request, "This patient is already admitted for this visit.")
        return _visit_back(visit)

    form = AdmitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            admission = admit_patient(visit, form.cleaned_data["bed"], form.cleaned_data["reason"], request.user)
        except EXPECTED_ERRORS as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Admitted as {admission.number}, {admission.bed}.")
            return _visit_back(visit)
    return render(request, "wards/admit.html", {"form": form, "patient": visit.patient, "visit": visit})


@role_required(*NURSE_OR_DOCTOR)
@require_POST
def transfer_view(request, pk):
    """Move an admitted patient to another free bed."""
    admission = get_object_or_404(Admission.objects.select_related("patient", "bed"), pk=pk)
    form = TransferForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a free bed to move the patient to.")
    else:
        try:
            transfer_patient(admission, form.cleaned_data["bed"], request.user)
        except EXPECTED_ERRORS as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{admission.patient.full_name} moved to {admission.bed}.")
    return redirect("ward_board")


@role_required(*DOCTOR)
def discharge_view(request, pk):
    """Discharge with a summary. Billing must be clear, unless an administrator overrides it."""
    admission = get_object_or_404(
        Admission.objects.select_related("patient", "bed", "appointment"), pk=pk
    )
    visit = admission.appointment
    if not admission.is_active:
        messages.error(request, "This patient has already been discharged.")
        return _visit_back(visit)

    cleared, clearance_reason = clearance_status(visit)
    can_manage = request.user.has_role(Role.ADMIN)
    form = DischargeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        overridden = False
        if not cleared:
            if can_manage and request.POST.get("override_clearance") == "1":
                overridden = True
            else:
                messages.error(request, f"Financial clearance required: {clearance_reason}")
                return redirect("discharge", pk=admission.pk)
        try:
            discharge_patient(admission, form.cleaned_data["summary"], request.user)
        except EXPECTED_ERRORS as exc:
            messages.error(request, str(exc))
        else:
            if overridden:
                log_access(admission, f"discharged with clearance override: {clearance_reason}")
            messages.success(request, f"{admission.patient.full_name} discharged. Bed freed.")
            return _visit_back(visit)

    return render(
        request,
        "wards/discharge.html",
        {
            "admission": admission,
            "form": form,
            "cleared": cleared,
            "clearance_reason": clearance_reason,
            "can_manage": can_manage,
        },
    )
