from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.appointments.models import Appointment, Status, TransitionBlocked
from apps.core.audit import log_access
from apps.core.permissions import role_required

from .forms import AdmitForm, DischargeForm, TransferForm
from .models import Admission, Bed, BedUnavailable, Ward, admit, discharge, transfer


@role_required(Role.NURSE, Role.DOCTOR, Role.RECEPTION)
def board(request):
    wards = Ward.objects.prefetch_related("beds")
    active = {a.bed_id: a for a in Admission.objects.filter(discharged_at__isnull=True).select_related("patient")}
    rows = [(ward, [(bed, active.get(bed.pk)) for bed in ward.beds.all()]) for ward in wards]
    counts = {
        "free": Bed.objects.filter(status=Bed.Status.FREE).count(),
        "occupied": Bed.objects.filter(status=Bed.Status.OCCUPIED).count(),
    }
    return render(
        request,
        "wards/board.html",
        {
            "rows": rows,
            "counts": counts,
            "can_move": request.user.has_role(Role.NURSE),
            "transfer_form": TransferForm(),
        },
    )


@role_required(Role.DOCTOR)
@require_POST
def admit_view(request, pk):
    visit = get_object_or_404(Appointment, pk=pk)
    if visit.status != Status.WITH_DOCTOR:
        raise PermissionDenied
    form = AdmitForm(request.POST)
    if form.is_valid():
        try:
            admission = admit(visit, form.cleaned_data["bed"], form.cleaned_data["reason"], request.user)
        except (BedUnavailable, TransitionBlocked) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Admitted to {admission.bed}: {admission.number}.")
    else:
        messages.error(request, "Choose a free bed and give a reason.")
    return redirect("visit_detail", pk=visit.pk)


@role_required(Role.NURSE)
@require_POST
def transfer_view(request, pk):
    admission = get_object_or_404(Admission, pk=pk)
    form = TransferForm(request.POST)
    if form.is_valid():
        try:
            transfer(admission, form.cleaned_data["bed"], request.user)
        except BedUnavailable as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{admission.patient.full_name} moved to {form.cleaned_data['bed']}.")
    else:
        messages.error(request, "Choose a free bed.")
    return redirect("ward_board")


@role_required(Role.DOCTOR)
def discharge_view(request, pk):
    admission = get_object_or_404(Admission.objects.select_related("patient", "bed__ward"), pk=pk)
    form = DischargeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            discharge(admission, form.cleaned_data["summary"], request.user)
        except (BedUnavailable, TransitionBlocked, PermissionError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{admission.patient.full_name} discharged; the bed is free.")
            return redirect("ward_board")
    log_access(admission)
    return render(request, "wards/discharge.html", {"admission": admission, "form": form})
