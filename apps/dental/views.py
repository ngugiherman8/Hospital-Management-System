from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.appointments.models import Appointment, Clinic
from apps.clinical.models import Consultation
from apps.clinical.views import EDITABLE
from apps.core.audit import log_access
from apps.core.permissions import role_required
from apps.maternity.models import PregnancyEpisode

from .forms import ConsentForm, DentalVisitForm, PlanItemForm, ToothForm
from .models import (
    DentalVisit,
    ToothRecord,
    TreatmentPlan,
    TreatmentPlanItem,
    current_chart,
)

VIEW = (Role.DENTIST, Role.DOCTOR)


def _visit(pk):
    return get_object_or_404(Appointment.objects.select_related("patient"), pk=pk, clinic=Clinic.DENTAL)


def _dentist_visit(request, pk):
    """The appointment and its dental visit, for a dentist who may change them."""
    if not request.user.has_role(Role.DENTIST):
        raise PermissionDenied
    appointment = _visit(pk)
    if appointment.status not in EDITABLE:
        raise PermissionDenied
    consultation, _ = Consultation.objects.get_or_create(appointment=appointment, defaults={"doctor": request.user})
    dental, _ = DentalVisit.objects.get_or_create(consultation=consultation, defaults={"dentist": request.user})
    return appointment, dental


def _rows(teeth, per_row):
    return [teeth[i : i + per_row] for i in range(0, len(teeth), per_row)]


@role_required(*VIEW)
def dental_visit(request, pk):
    appointment = _visit(pk)
    patient = appointment.patient
    can_edit = request.user.has_role(Role.DENTIST) and appointment.status in EDITABLE
    consultation = getattr(appointment, "consultation", None)
    dental = getattr(consultation, "dental", None) if consultation else None
    if dental is None and can_edit:
        appointment, dental = _dentist_visit(request, pk)
    if dental:
        log_access(dental)
    chart = current_chart(patient)
    teeth_upper = [18, 17, 16, 15, 14, 13, 12, 11, 21, 22, 23, 24, 25, 26, 27, 28]
    teeth_lower = [48, 47, 46, 45, 44, 43, 42, 41, 31, 32, 33, 34, 35, 36, 37, 38]
    plan = patient.treatment_plans.filter(status="open").first() or patient.treatment_plans.first()
    ctx = {
        "visit": appointment,
        "patient": patient,
        "dental": dental,
        "can_edit": can_edit,
        "chart": chart,
        "upper": teeth_upper,
        "lower": teeth_lower,
        "baby_upper": [55, 54, 53, 52, 51, 61, 62, 63, 64, 65],
        "baby_lower": [85, 84, 83, 82, 81, 71, 72, 73, 74, 75],
        "history": ToothRecord.objects.filter(patient=patient).select_related("recorded_by", "visit")[:30],
        "plan": plan,
        "pregnant": PregnancyEpisode.objects.filter(patient=patient, status="active").exists(),
        "ready_total": sum(
            (i.estimate for p in patient.treatment_plans.all() for i in p.items.all() if i.ready_for_billing), 0
        ),
        "visit_form": DentalVisitForm(instance=dental) if can_edit and dental else None,
        "tooth_form": ToothForm(),
        "item_form": PlanItemForm(),
        "consent_form": ConsentForm(),
    }
    return render(request, "dental/visit.html", ctx)


def _errors(form):
    return " ".join(e for errs in form.errors.values() for e in errs)


@role_required(Role.DENTIST)
@require_POST
def visit_save(request, pk):
    appointment, dental = _dentist_visit(request, pk)
    form = DentalVisitForm(request.POST, instance=dental)
    if form.is_valid():
        form.save()
        messages.success(request, "Dental visit saved.")
    else:
        messages.error(request, _errors(form))
    return redirect("dental_visit", pk=appointment.pk)


@role_required(Role.DENTIST)
@require_POST
def tooth_add(request, pk):
    appointment, dental = _dentist_visit(request, pk)
    form = ToothForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        ToothRecord.objects.create(
            patient=appointment.patient,
            visit=dental,
            tooth=data["tooth"],
            surface=data["surface"],
            condition=data["condition"],
            recorded_by=request.user,
        )
        messages.success(request, f"Tooth {data['tooth']} recorded.")
    else:
        messages.error(request, _errors(form))
    return redirect("dental_visit", pk=appointment.pk)


@role_required(Role.DENTIST)
@require_POST
def plan_item_add(request, pk):
    appointment, dental = _dentist_visit(request, pk)
    form = PlanItemForm(request.POST)
    if form.is_valid():
        plan = appointment.patient.treatment_plans.filter(status="open").first() or TreatmentPlan.objects.create(
            patient=appointment.patient, created_by=request.user
        )
        if plan.has_consent:
            # Adding work changes the estimate, so the patient must agree again.
            plan.consent_at = plan.estimate_shown_at = None
            plan.consent_by = ""
            plan.save()
            messages.warning(request, "The plan changed, so consent must be recorded again before work starts.")
        TreatmentPlanItem.objects.create(
            plan=plan,
            tooth=form.cleaned_data["tooth"],
            procedure=form.cleaned_data["procedure"],
            estimate=form.cleaned_data["estimate"],
        )
        messages.success(request, "Added to the treatment plan.")
    else:
        messages.error(request, _errors(form))
    return redirect("dental_visit", pk=appointment.pk)


@role_required(Role.DENTIST)
@require_POST
def plan_consent(request, pk, plan_pk):
    appointment, dental = _dentist_visit(request, pk)
    plan = get_object_or_404(TreatmentPlan, pk=plan_pk, patient=appointment.patient)
    form = ConsentForm(request.POST)
    if form.is_valid():
        try:
            plan.record_consent(request.user, form.cleaned_data["agreed_by"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"Consent recorded. Estimate: {plan.total_estimate}.")
    else:
        messages.error(request, _errors(form))
    return redirect("dental_visit", pk=appointment.pk)


@role_required(Role.DENTIST)
@require_POST
def plan_item_action(request, pk, item_pk):
    appointment, dental = _dentist_visit(request, pk)
    item = get_object_or_404(TreatmentPlanItem, pk=item_pk, plan__patient=appointment.patient)
    action = request.POST.get("action")
    try:
        if action == "start":
            item.start(request.user)
        elif action == "complete":
            item.complete(request.user, dental, request.POST.get("anaesthetic_note", ""))
            messages.success(request, f"{item} is done and ready for billing.")
        elif action == "decline":
            item.decline()
        else:
            raise PermissionDenied
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("dental_visit", pk=appointment.pk)
