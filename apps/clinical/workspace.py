"""Shared helpers for the pages that show one visit (consultation, admission, past visit)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.appointments.models import TransitionError
from apps.core.permissions import RoleRequiredMixin
from apps.orders.forms import OrderForm
from apps.pharmacy_rx.forms import PrescriptionForm
from apps.wards.forms import DischargeForm, TransferForm
from apps.wards.models import Admission

from .forms import NoteForm, VitalsForm
from .models import ClinicalNote, Visit

DOCTOR = ("doctor",)
NURSE = ("nurse",)
CARE = ("doctor", "nurse")
LAB = ("lab",)
BREAK_GLASS_MINUTES = 30


def get_admission(visit):
    return Admission.objects.filter(visit=visit).select_related("bed__ward").first()


def visit_url(visit):
    adm = get_admission(visit)
    if adm:
        return reverse("wards:admission", args=[adm.pk])
    if visit.queue_entry_id:
        return reverse("clinical:consult", args=[visit.queue_entry_id])
    return reverse("clinical:visit", args=[visit.pk])


def safe_next(request, default):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        return nxt
    return default


def has_clinical_access(request, patient):
    """Care staff always; everyone else only with a recent, logged break-glass override."""
    if request.user.has_role(*CARE):
        return True
    ts = request.session.get("break_glass", {}).get(str(patient.pk))
    return bool(ts and timezone.now().timestamp() - ts < BREAK_GLASS_MINUTES * 60)


def workspace_context(request, visit, **extra):
    patient, user = visit.patient, request.user
    orders = list(visit.orders.select_related("test", "ordered_by"))
    for o in orders:
        o.result = o.current_result
    ctx = {
        "visit": visit,
        "patient": patient,
        "admission": get_admission(visit),
        "entry": visit.queue_entry,
        "cover": patient.covers.first(),
        "vitals": visit.vitals.all(),
        "latest": visit.latest_vitals,
        "history_note": visit.current_note("history"),
        "exam_note": visit.current_note("examination"),
        "plan_note": visit.current_note("plan"),
        "progress_notes": ClinicalNote.current_for(visit, "progress"),
        "diagnoses": visit.diagnoses.select_related("icd10"),
        "orders": orders,
        "prescriptions": visit.prescriptions.select_related("drug"),
        "can_edit": visit.is_open and user.has_role(*DOCTOR),
        "can_note": visit.is_open and user.has_role(*CARE),
        "can_transfer": visit.is_open and user.has_role(*NURSE),
        "next": request.get_full_path(),
        "past_visit_count": Visit.objects.filter(patient=patient).exclude(pk=visit.pk).count(),
        "order_form": OrderForm(),
        "rx_form": PrescriptionForm(patient=patient, visit=visit),
        "note_form": NoteForm(),
        "vitals_form": VitalsForm(),
        "open_orders": sum(1 for o in orders if o.is_open),
    }
    ctx.update(extra)
    return ctx


def render_visit(request, visit, **extra):
    ctx = workspace_context(request, visit, **extra)
    if ctx["admission"]:
        ctx.setdefault("transfer_form", TransferForm())
        ctx.setdefault("discharge_form", DischargeForm())
        return render(request, "clinical/admission.html", ctx)
    return render(request, "clinical/consultation.html", ctx)


class VisitPostView(RoleRequiredMixin, View):
    """Base for the small POST endpoints that add things to an open visit."""

    allowed_roles = DOCTOR
    http_method_names = ["post"]

    def post(self, request, pk, **kwargs):
        self.visit = get_object_or_404(Visit.objects.select_related("patient"), pk=pk)
        self.back = safe_next(request, visit_url(self.visit))
        if not self.visit.is_open:
            messages.error(request, "This visit is closed, so nothing more can be added to it.")
            return redirect(self.back)
        return self.act(request, **kwargs)

    def invalid(self, request, **forms):
        messages.error(request, "That was not saved. Check the highlighted fields.")
        return render_visit(request, self.visit, **forms)

    def step(self, request, entry, action, **ctx):
        """Take a TRANSITIONS step and report why it was refused. Returns True when it moved."""
        try:
            entry.apply(action, request.user, **ctx)
            return True
        except TransitionError as e:
            messages.error(request, str(e))
            return False
