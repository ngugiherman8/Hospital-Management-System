from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.appointments.models import QueueEntry, Stage, TransitionError
from apps.core.audit import log_access, log_event
from apps.core.permissions import RoleRequiredMixin
from apps.patients.models import Patient

from .forms import (
    AmendForm,
    BreakGlassForm,
    ConsultForm,
    DiagnosisAddForm,
    NoteForm,
    TriageForm,
    VitalsForm,
)
from .models import ClinicalNote, ICD10Code, Outcome, Visit, VisitDiagnosis
from .workspace import (
    CARE,
    DOCTOR,
    NURSE,
    VisitPostView,
    has_clinical_access,
    render_visit,
    safe_next,
    workspace_context,
)

HISTORY_ROLES = ("receptionist", "doctor", "nurse", "lab", "pharmacist", "cashier")


class TriageView(RoleRequiredMixin, View):
    """Nurses record triage. Doctors and reception may open it read-only."""

    allowed_roles = ("nurse", "doctor", "receptionist")

    def _page(self, request, entry, visit, form, readonly):
        return render(
            request,
            "clinical/triage.html",
            {
                "entry": entry,
                "patient": entry.patient,
                "visit": visit,
                "form": form,
                "readonly": readonly,
                "cover": entry.patient.covers.first(),
                "last_visit": Visit.objects.filter(patient=entry.patient)
                .exclude(pk=visit.pk)
                .order_by("-started_at")
                .first(),
                "pain_range": range(0, 11),
            },
        )

    def get(self, request, entry_pk):
        entry = get_object_or_404(QueueEntry.objects.select_related("patient", "clinic"), pk=entry_pk)
        is_nurse = request.user.has_role(*NURSE)
        visit = Visit.for_queue_entry(entry) if is_nurse else Visit.objects.filter(queue_entry=entry).first()
        if visit is None or (not is_nurse and not visit.triaged_at):
            messages.info(request, "Triage has not been done for this patient yet.")
            return redirect("queue")
        if is_nurse and entry.stage not in (Stage.WAITING_TRIAGE, Stage.WAITING_DOCTOR):
            messages.info(request, f"{entry.ticket} has already moved past triage.")
            return redirect("queue")
        log_access(visit)
        initial = {"complaint": visit.complaint, "duration": visit.duration, "priority": visit.priority}
        tv = visit.vitals.filter(context="triage").first()
        if tv:
            initial.update(VitalsForm.initial_from(tv))
        return self._page(request, entry, visit, TriageForm(initial=initial), readonly=not is_nurse)

    def post(self, request, entry_pk):
        if not request.user.has_role(*NURSE):
            raise PermissionDenied
        entry = get_object_or_404(QueueEntry.objects.select_related("patient", "clinic"), pk=entry_pk)
        if entry.stage not in (Stage.WAITING_TRIAGE, Stage.WAITING_DOCTOR):
            messages.info(request, f"{entry.ticket} has already moved past triage.")
            return redirect("queue")
        visit = Visit.for_queue_entry(entry)
        form = TriageForm(request.POST)
        if not form.is_valid():
            return self._page(request, entry, visit, form, readonly=False)
        with transaction.atomic():
            tv = visit.vitals.filter(context="triage").first()
            if tv:
                vs = form.to_vitals(instance=tv)
            else:
                vs = form.to_vitals(visit=visit, recorded_by=request.user, context="triage")
            d = form.cleaned_data
            visit.complaint, visit.duration, visit.priority = d["complaint"], d["duration"], d["priority"]
            visit.triaged_by, visit.triaged_at = request.user, timezone.now()
            visit.save()
            if entry.stage == Stage.WAITING_TRIAGE:
                try:
                    entry.apply("save_triage", request.user)
                except TransitionError as e:
                    messages.error(request, str(e))
                    return redirect("queue")
        if vs.is_critical and visit.priority == "routine":
            messages.warning(request, "Critical readings recorded but priority is Routine. Consider Emergency.")
        messages.success(request, f"{entry.patient.full_name} triaged and sent to the doctor.")
        return redirect("queue")


class CallInView(RoleRequiredMixin, View):
    allowed_roles = DOCTOR
    http_method_names = ["post"]

    def post(self, request, entry_pk):
        entry = get_object_or_404(QueueEntry, pk=entry_pk)
        if entry.stage == Stage.WAITING_DOCTOR:
            try:
                entry.doctor = request.user
                entry.apply("call_in", request.user, room=request.POST.get("room", "") or "1")
            except TransitionError as e:
                messages.error(request, str(e))
                return redirect("queue")
            visit = Visit.for_queue_entry(entry)
            visit.doctor = request.user
            visit.save()
        elif entry.stage != Stage.WITH_DOCTOR:
            messages.error(request, f"{entry.ticket} is not waiting for a doctor.")
            return redirect("queue")
        return redirect("clinical:consult", entry_pk=entry.pk)


class ConsultView(RoleRequiredMixin, View):
    """Doctors write the consultation. Nurses may open it read-only."""

    allowed_roles = ("doctor", "nurse")

    def get(self, request, entry_pk):
        entry = get_object_or_404(QueueEntry.objects.select_related("patient"), pk=entry_pk)
        if entry.stage in (Stage.WAITING_TRIAGE, Stage.WAITING_DOCTOR):
            messages.info(request, "Call the patient in from the queue first.")
            return redirect("queue")
        visit = Visit.for_queue_entry(entry)
        if visit.is_open and visit.doctor_id is None and request.user.has_role(*DOCTOR):
            visit.doctor = request.user
            visit.save()
        log_access(visit)
        form = ConsultForm(
            initial={
                "history": getattr(visit.current_note("history"), "text", ""),
                "examination": getattr(visit.current_note("examination"), "text", ""),
                "plan": getattr(visit.current_note("plan"), "text", ""),
                "outcome": visit.outcome,
                "review_in_days": visit.review_in_days,
                "referred_to": visit.referred_to,
            }
        )
        return render_visit(request, visit, consult_form=form)

    def post(self, request, entry_pk):
        if not request.user.has_role(*DOCTOR):
            raise PermissionDenied
        entry = get_object_or_404(QueueEntry.objects.select_related("patient"), pk=entry_pk)
        visit = Visit.for_queue_entry(entry)
        if not visit.is_open:
            messages.error(request, "This consultation is closed.")
            return redirect("queue")
        form = ConsultForm(request.POST)
        if not form.is_valid():
            return render_visit(request, visit, consult_form=form)
        d = form.cleaned_data
        with transaction.atomic():
            for kind in ("history", "examination", "plan"):
                text = d[kind].strip()
                current = visit.current_note(kind)
                if text and (not current or current.text != text):
                    if current:
                        current.amend(text, request.user, "Updated during consultation")
                    else:
                        ClinicalNote.objects.create(visit=visit, kind=kind, text=text, author=request.user)
            visit.outcome = d["outcome"] or ""
            visit.review_in_days = d["review_in_days"]
            visit.referred_to = d["referred_to"]
            visit.save()

        if request.POST.get("action") != "finish":
            messages.success(request, "Draft saved.")
            return redirect("clinical:consult", entry_pk=entry.pk)
        if visit.outcome == Outcome.ADMIT:
            if not visit.diagnoses.exists():
                messages.error(request, "Add at least one diagnosis first.")
                return redirect("clinical:consult", entry_pk=entry.pk)
            return redirect("wards:admit_visit", visit_pk=visit.pk)
        try:
            entry.apply("complete", request.user)
        except TransitionError as e:
            messages.error(request, str(e))
            return redirect("clinical:consult", entry_pk=entry.pk)
        visit.finished_at = timezone.now()
        visit.save()
        visit.close()
        if visit.orders.filter(status__in=["ordered", "in_progress"]).exists():
            messages.warning(request, "Some tests are still pending. Results will appear on the visit record.")
        messages.success(
            request, f"Consultation for {entry.patient.full_name} finished. Prescriptions go to the pharmacy."
        )
        return redirect("queue")


class IcdSearchView(RoleRequiredMixin, View):
    allowed_roles = DOCTOR

    def get(self, request, pk):
        visit = get_object_or_404(Visit, pk=pk)
        q = request.GET.get("q", "").strip()
        results = []
        if len(q) >= 2:
            results = ICD10Code.objects.filter(Q(code__istartswith=q) | Q(title__icontains=q)).order_by("code")[:8]
        return render(
            request,
            "clinical/_icd_results.html",
            {"results": results, "visit": visit, "q": q, "next": request.GET.get("next", "")},
        )


class DiagnosisAddView(VisitPostView):
    def act(self, request):
        form = DiagnosisAddForm(request.POST)
        if form.is_valid():
            code = get_object_or_404(ICD10Code, pk=form.cleaned_data["icd10"])
            VisitDiagnosis.objects.get_or_create(
                visit=self.visit,
                icd10=code,
                defaults={"kind": form.cleaned_data["kind"] or "provisional", "added_by": request.user},
            )
        return redirect(self.back)


class DiagnosisActionView(VisitPostView):
    def act(self, request, diag_pk, action):
        diag = get_object_or_404(VisitDiagnosis, pk=diag_pk, visit=self.visit)
        if action == "remove":
            diag.delete()
        elif action == "confirm":
            diag.kind = "confirmed"
            diag.save()
        else:
            raise Http404
        return redirect(self.back)


class ProgressNoteView(VisitPostView):
    allowed_roles = CARE

    def act(self, request):
        form = NoteForm(request.POST)
        if form.is_valid():
            ClinicalNote.objects.create(
                visit=self.visit, kind="progress", text=form.cleaned_data["text"].strip(), author=request.user
            )
        else:
            messages.error(request, "Write the note before saving.")
        return redirect(self.back)


class NoteAmendView(VisitPostView):
    allowed_roles = CARE

    def act(self, request, note_pk):
        note = get_object_or_404(ClinicalNote, pk=note_pk, visit=self.visit)
        if note.amended_by.exists():
            messages.error(request, "That note has already been amended. Amend the latest version.")
            return redirect(self.back)
        form = AmendForm(request.POST)
        if form.is_valid():
            note.amend(form.cleaned_data["text"].strip(), request.user, form.cleaned_data["reason"])
            messages.success(request, "Note amended. The earlier version is kept in the record.")
        else:
            messages.error(request, "An amendment needs the corrected text and a reason.")
        return redirect(self.back)


class VitalsAddView(VisitPostView):
    allowed_roles = CARE

    def act(self, request):
        form = VitalsForm(request.POST)
        if not form.is_valid():
            return self.invalid(request, vitals_form=form)
        form.to_vitals(visit=self.visit, recorded_by=request.user, context="ward")
        messages.success(request, "Observations recorded.")
        return redirect(self.back)


class SendToLabView(VisitPostView):
    def act(self, request):
        entry = self.visit.queue_entry
        if entry is None:
            messages.error(request, "This visit is not in the outpatient queue.")
        elif self.step(request, entry, "send_to_lab"):
            messages.success(request, "Patient sent to the lab. They return to you when results are in.")
        return redirect(self.back)


class PatientHistoryView(RoleRequiredMixin, View):
    """The patient timeline. Care staff open it directly; others need a logged break-glass override."""

    allowed_roles = HISTORY_ROLES

    def get(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        if not has_clinical_access(request, patient):
            return render(
                request,
                "clinical/break_glass.html",
                {"patient": patient, "form": BreakGlassForm(), "next": request.get_full_path()},
                status=403,
            )
        log_access(patient, reason="Clinical history")
        visits = list(
            patient.visits.prefetch_related(
                "diagnoses__icd10", "orders__test", "orders__results", "prescriptions__drug"
            )
            .select_related("doctor")
            .order_by("-started_at")
        )
        for v in visits:
            for o in v.orders.all():
                o.result = o.current_result
        return render(request, "clinical/history.html", {"patient": patient, "visits": visits})


class BreakGlassView(RoleRequiredMixin, View):
    allowed_roles = HISTORY_ROLES
    http_method_names = ["post"]

    def post(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        form = BreakGlassForm(request.POST)
        back = safe_next(request, reverse("clinical:history", args=[patient.pk]))
        if not form.is_valid():
            return render(
                request, "clinical/break_glass.html", {"patient": patient, "form": form, "next": back}, status=403
            )
        log_event("break_glass", patient, reason=form.cleaned_data["reason"], needs_review=True)
        grants = request.session.get("break_glass", {})
        grants[str(patient.pk)] = timezone.now().timestamp()
        request.session["break_glass"] = grants
        messages.warning(request, "Emergency access granted for 30 minutes. It has been logged and will be reviewed.")
        return redirect(back)


class VisitDetailView(RoleRequiredMixin, View):
    allowed_roles = HISTORY_ROLES

    def get(self, request, pk):
        visit = get_object_or_404(Visit.objects.select_related("patient"), pk=pk)
        if not has_clinical_access(request, visit.patient):
            return render(
                request,
                "clinical/break_glass.html",
                {"patient": visit.patient, "form": BreakGlassForm(), "next": request.get_full_path()},
                status=403,
            )
        log_access(visit)
        return render(request, "clinical/visit_detail.html", workspace_context(request, visit))
