import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core.permissions import role_required
from apps.patients.models import Patient

from .forms import AppointmentForm
from .models import Appointment, Status, TransitionBlocked

STAFF = (
    Role.RECEPTION, Role.NURSE, Role.DOCTOR, Role.LAB, Role.PHARMACIST, Role.CASHIER,
    Role.DENTIST, Role.MIDWIFE, Role.RADIOGRAPHER, Role.RADIOLOGIST,
)  # fmt: skip


def _queue_moves(visit, user):
    """Buttons the queue shows. Moves that need a form (triage, admit, finish) live on their own screens."""
    hidden = {Status.ADMITTED, Status.AWAITING_RESULTS}
    if visit.status == Status.WAITING_TRIAGE:
        hidden.add(Status.WAITING_DOCTOR)  # the triage screen does this when it saves
    if visit.status in (Status.WITH_DOCTOR, Status.ADMITTED):
        hidden.add(Status.COMPLETED)  # needs a diagnosis or a discharge summary
    return [s for s in visit.allowed_next(user) if s not in hidden]


@role_required(*STAFF)
def appointment_list(request):
    try:
        day = datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    stage = request.GET.get("stage", "")
    appointments = (
        Appointment.objects.filter(visit_date=day)
        .select_related("patient", "doctor", "triage")
        .annotate(
            rank=Case(
                When(Q(triage__priority="emergency") | ~Q(emergency_reason=""), then=Value(0)),
                When(triage__priority="urgent", then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("rank", "arrived_at", "queue_number", "scheduled_for")
    )
    counts = dict(appointments.values_list("status").annotate(n=Count("id")))
    if stage in Status.values:
        appointments = appointments.filter(status=stage)
    rows = [(a, _queue_moves(a, request.user)) for a in appointments]
    return render(
        request,
        "appointments/list.html",
        {
            "rows": rows,
            "day": day,
            "is_today": day == timezone.localdate(),
            "stage": stage,
            "statuses": Status,
            "counts": counts,
            "waiting_triage": counts.get(Status.WAITING_TRIAGE, 0),
            "waiting_doctor": counts.get(Status.WAITING_DOCTOR, 0),
            "with_doctor": counts.get(Status.WITH_DOCTOR, 0) + counts.get(Status.AWAITING_RESULTS, 0),
            "completed": counts.get(Status.COMPLETED, 0),
        },
    )


@role_required(Role.RECEPTION)
def appointment_create(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    form = AppointmentForm(request.POST or None, patient=patient, initial={"kind": Appointment.Kind.WALK_IN})
    if request.method == "POST" and form.is_valid():
        appointment = form.save(commit=False)
        appointment.patient = patient
        appointment.created_by = request.user
        appointment.save()
        messages.success(request, f"{patient.full_name} added. {appointment.queue_label or 'Appointment booked.'}")
        return redirect("appointment_list")
    return render(request, "appointments/form.html", {"form": form, "patient": patient})


@role_required(*STAFF)
@require_POST
def appointment_status(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)
    try:
        appointment.move_to(request.POST.get("status", ""), request.user)
    except PermissionError:
        raise PermissionDenied from None
    except TransitionBlocked as exc:
        messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "appointment_list")
    messages.success(request, f"{appointment.patient.full_name}: {appointment.get_status_display()}.")
    return redirect(request.POST.get("next") or "appointment_list")


@role_required(Role.RECEPTION, Role.NURSE, Role.DOCTOR)
@require_POST
def appointment_fast_track(request, pk):
    """Emergency: send a patient to the doctor before triage. The reason is kept on the visit and audited."""
    appointment = get_object_or_404(Appointment, pk=pk)
    try:
        appointment.fast_track(request.user, request.POST.get("reason", ""))
    except PermissionError:
        raise PermissionDenied from None
    except TransitionBlocked as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{appointment.patient.full_name} sent to the doctor as an emergency.")
    return redirect("appointment_list")
