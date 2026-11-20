from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import CLINICIAN_ROLES, Role
from apps.appointments.models import Appointment, Status, TransitionBlocked
from apps.core.audit import log_access
from apps.core.permissions import role_required
from apps.imaging.forms import ImagingOrderForm
from apps.imaging.models import ImagingStudy
from apps.orders.forms import OrderForm
from apps.pharmacy_rx.forms import PrescribeForm
from apps.wards.forms import AdmitForm

from .forms import DiagnosisForm, NoteForm, OutcomeForm, TriageForm
from .icd10 import search_icd10
from .models import Consultation

# Visits in these states can still have notes, tests and prescriptions added by the doctor.
EDITABLE = (Status.WITH_DOCTOR, Status.AWAITING_RESULTS, Status.ADMITTED)


def _visit(pk):
    return get_object_or_404(Appointment.objects.select_related("patient", "doctor"), pk=pk)


@role_required(Role.NURSE, Role.DOCTOR, Role.RECEPTION)
def triage(request, pk):
    visit = _visit(pk)
    record = getattr(visit, "triage", None)
    can_edit = request.user.has_role(Role.NURSE) and visit.status == Status.WAITING_TRIAGE
    form = TriageForm(request.POST or None, instance=record)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            try:
                with transaction.atomic():
                    saved = form.save(commit=False)
                    saved.appointment = visit
                    saved.nurse = request.user
                    saved.save()
                    visit.refresh_from_db()
                    visit.move_to(Status.WAITING_DOCTOR, request.user)
            except TransitionBlocked as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{visit.patient.full_name} sent to the doctor.")
                return redirect("appointment_list")
    if record:
        log_access(record)
    return render(
        request, "clinical/triage.html", {"visit": visit, "form": form, "record": record, "can_edit": can_edit}
    )


def _doctor_consultation(request, pk):
    """The visit and its consultation for a doctor who may change them, else 403."""
    if not request.user.has_role(*CLINICIAN_ROLES):
        raise PermissionDenied
    visit = _visit(pk)
    _clinic_guard(request.user, visit)
    if visit.status not in EDITABLE:
        raise PermissionDenied
    consultation, _ = Consultation.objects.get_or_create(appointment=visit, defaults={"doctor": request.user})
    return visit, consultation


def _clinic_ok(user, visit):
    """Dentists work dental visits and midwives work maternal and child health visits; doctors any visit."""
    if user.has_role(Role.DOCTOR):
        return True
    if user.role == Role.DENTIST:
        return visit.clinic == "dental"
    if user.role == Role.MIDWIFE:
        return visit.clinic == "mch"
    return False


def _clinic_guard(user, visit):
    if not _clinic_ok(user, visit):
        raise PermissionDenied


@role_required(Role.NURSE, *CLINICIAN_ROLES)
def visit_detail(request, pk):
    visit = _visit(pk)
    is_doctor = _clinic_ok(request.user, visit)
    if request.user.role in (Role.DENTIST, Role.MIDWIFE) and not is_doctor:
        raise PermissionDenied  # dentists and midwives open only their own clinic's visits
    consultation = getattr(visit, "consultation", None)
    if consultation is None and is_doctor and visit.status in EDITABLE:
        consultation = Consultation.objects.create(appointment=visit, doctor=request.user)
    can_edit = is_doctor and visit.status in EDITABLE
    ctx = {
        "visit": visit,
        "patient": visit.patient,
        "triage": getattr(visit, "triage", None),
        "consultation": consultation,
        "can_edit": can_edit,
        "moves": [s for s in visit.allowed_next(request.user) if s != Status.ADMITTED],
    }
    if consultation:
        log_access(consultation)
        ctx.update(
            notes=consultation.notes.all(),
            diagnoses=consultation.diagnoses.all(),
            prescriptions=consultation.prescriptions.prefetch_related("items__drug"),
        )
    ctx["orders"] = visit.orders.prefetch_related("items__test", "items__results", "items__study")
    ctx["studies"] = ImagingStudy.objects.filter(order_item__order__appointment=visit).select_related(
        "item__test", "item__modality"
    )
    if can_edit:
        note = consultation.current_note
        ctx.update(
            note_form=NoteForm(initial={f: getattr(note, f) for f in NoteForm.Meta.fields} if note else None),
            diagnosis_form=DiagnosisForm(),
            outcome_form=OutcomeForm(instance=consultation),
            order_form=OrderForm(),
            imaging_form=ImagingOrderForm(patient=visit.patient),
            rx_form=PrescribeForm(patient=visit.patient),
        )
        ctx["admit_form"] = AdmitForm()
    admission = getattr(visit, "admission", None)
    ctx["admission"] = admission
    if admission:
        log_access(admission)
    return render(request, "clinical/visit.html", ctx)


def _back(visit):
    return redirect("visit_detail", pk=visit.pk)


@role_required(*CLINICIAN_ROLES)
@require_POST
def note_save(request, pk):
    visit, consultation = _doctor_consultation(request, pk)
    form = NoteForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.consultation = consultation
        note.author = request.user
        note.save()
        messages.success(request, f"Note saved as version {note.version}.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return _back(visit)


@role_required(*CLINICIAN_ROLES)
@require_POST
def diagnosis_add(request, pk):
    visit, consultation = _doctor_consultation(request, pk)
    form = DiagnosisForm(request.POST)
    if form.is_valid():
        diagnosis = form.save(commit=False)
        diagnosis.consultation = consultation
        diagnosis.save()
        messages.success(request, "Diagnosis saved.")
    else:
        messages.error(request, "Enter a diagnosis name.")
    return _back(visit)


@role_required(*CLINICIAN_ROLES)
@require_POST
def outcome_save(request, pk):
    visit, consultation = _doctor_consultation(request, pk)
    form = OutcomeForm(request.POST, instance=consultation)
    if form.is_valid():
        form.save()
        messages.success(request, "Outcome saved.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return _back(visit)


@role_required(Role.NURSE, *CLINICIAN_ROLES)
def icd10_lookup(request):
    return JsonResponse({"results": [{"code": c, "name": n} for c, n in search_icd10(request.GET.get("q"))]})
