from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST

from apps.core.permissions import CLINICAL, FRONT_DESK, FRONT_DESK_AND_CLINICAL, RoleRequiredMixin
from apps.patients.models import Patient
from apps.patients.views import phone_search_q

from .forms import AppointmentForm
from .models import STAGE_GROUPS, Appointment, AppointmentStatus, Clinic, QueueEntry, Stage


class QueueView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK_AND_CLINICAL + ("lab", "pharmacist", "cashier")

    def get(self, request):
        today = timezone.localdate()
        entries = QueueEntry.objects.filter(queue_date=today).select_related("patient", "clinic")
        counts = entries.aggregate(
            triage=Count("id", filter=Q(stage=Stage.WAITING_TRIAGE)),
            doctor=Count("id", filter=Q(stage=Stage.WAITING_DOCTOR)),
            seen_by_doctor=Count("id", filter=Q(stage=Stage.WITH_DOCTOR)),
            done=Count("id", filter=Q(stage=Stage.DONE)),
        )
        tab = request.GET.get("tab", "all")
        shown = entries.exclude(stage=Stage.DONE)
        if tab in STAGE_GROUPS:
            shown = shown.filter(stage__in=STAGE_GROUPS[tab])
        q = request.GET.get("q", "").strip()
        if q:
            shown = shown.filter(Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q)
                                 | Q(patient__hospital_number__icontains=q) | Q(patient__phone__icontains=q)
                                 | Q(patient__in=Patient.objects.filter(phone_search_q(q))))
        ctx = {"entries": shown, "counts": counts, "tab": tab, "q": q, "today": timezone.localdate(),
               "tabs": [("all", "All"), ("triage", "Triage"), ("doctor", "Doctor"), ("lab", "Lab and pharmacy")]}
        template = "appointments/_queue_table.html" if request.htmx else "appointments/queue.html"
        return render(request, template, ctx)


class WalkInView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK

    def post(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        clinic = get_object_or_404(Clinic, pk=request.POST.get("clinic"))
        entry, created = QueueEntry.join(patient=patient, clinic=clinic)
        if created:
            messages.success(request, f"{patient.full_name} added to the queue as {entry.ticket}.")
        else:
            messages.info(request, f"{patient.full_name} is already in today's queue ({entry.ticket}).")
        return redirect("queue")


class StageView(RoleRequiredMixin, View):
    """Move a queue entry on: nurses start triage, doctors call patients in."""

    allowed_roles = CLINICAL
    transitions = {
        "start-triage": ({Stage.WAITING_TRIAGE}, Stage.WAITING_DOCTOR, ("admin", "nurse")),
        "call-in": ({Stage.WAITING_DOCTOR}, Stage.WITH_DOCTOR, ("admin", "doctor")),
        "to-lab": ({Stage.WITH_DOCTOR}, Stage.AT_LAB, ("admin", "doctor")),
        "finish": ({Stage.WITH_DOCTOR, Stage.AT_LAB}, Stage.DONE, ("admin", "doctor")),
    }

    def post(self, request, pk, action):
        from django.core.exceptions import PermissionDenied
        from django.http import Http404

        if action not in self.transitions:
            raise Http404
        entry = get_object_or_404(QueueEntry, pk=pk)
        allowed_from, to_stage, roles = self.transitions[action]
        if not request.user.has_role(*roles):
            raise PermissionDenied
        if entry.stage not in allowed_from:
            messages.error(request, f"{entry.ticket} is not at the right stage for that.")
        else:
            entry.move_to(to_stage, room=request.POST.get("room", ""))
        return redirect("queue")


class AppointmentListView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK_AND_CLINICAL

    def get(self, request):
        day = request.GET.get("date")
        try:
            day = timezone.datetime.strptime(day, "%Y-%m-%d").date() if day else timezone.localdate()
        except ValueError:
            day = timezone.localdate()
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
        qs = Appointment.objects.filter(starts_at__gte=start, starts_at__lt=start + timedelta(days=1)
                                        ).select_related("patient", "doctor", "clinic")
        if request.user.role == "doctor":
            qs = qs.filter(doctor=request.user)
        return render(request, "appointments/list.html", {"appointments": qs, "day": day})


class AppointmentCreateView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK

    def get(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        return render(request, "appointments/form.html", {"form": AppointmentForm(), "patient": patient})

    def post(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        form = AppointmentForm(request.POST)
        if form.is_valid():
            appt = form.save(commit=False)
            appt.patient, appt.created_by = patient, request.user
            appt.save()
            messages.success(request, "Appointment booked. An SMS reminder will be sent the day before.")
            return redirect("appointments:list")
        return render(request, "appointments/form.html", {"form": form, "patient": patient})


@require_POST
def appointment_status(request, pk, action):
    """Receptionist marks an appointment as arrived (joins the queue) or cancelled."""
    from django.core.exceptions import PermissionDenied

    if not request.user.is_authenticated:
        return redirect("login")
    if not request.user.has_role(*FRONT_DESK):
        raise PermissionDenied
    appt = get_object_or_404(Appointment, pk=pk)
    if appt.status != AppointmentStatus.BOOKED:
        messages.error(request, "Only booked appointments can be changed.")
    elif action == "arrived":
        appt.status = AppointmentStatus.ARRIVED
        appt.save()
        QueueEntry.join(patient=appt.patient, clinic=appt.clinic, doctor=appt.doctor, appointment=appt)
        messages.success(request, f"{appt.patient.full_name} has arrived and joined the queue.")
    elif action == "cancel":
        appt.status = AppointmentStatus.CANCELLED
        appt.save()
        messages.info(request, "Appointment cancelled.")
    return redirect("appointments:list")
