from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.appointments.models import TransitionError
from apps.clinical.models import Outcome, Visit
from apps.clinical.workspace import CARE, DOCTOR, NURSE, VisitPostView, get_admission, render_visit
from apps.core.audit import log_access
from apps.core.permissions import RoleRequiredMixin
from apps.patients.models import Patient

from .forms import AdmitForm, DischargeForm, TransferForm
from .models import Admission, Bed, BedMove, Ward


class WardBoardView(RoleRequiredMixin, View):
    """Beds and who is in them. Nurses release cleaned beds; doctors and reception can look."""

    allowed_roles = ("nurse", "doctor", "receptionist")

    def get(self, request):
        admitted = {a.bed_id: a for a in Admission.objects.filter(status="admitted").select_related("patient")}
        wards, total, occupied = [], 0, 0
        for ward in Ward.objects.filter(is_active=True).prefetch_related("beds"):
            beds = [{"bed": b, "adm": admitted.get(b.pk)} for b in ward.beds.all() if b.is_active]
            n_occ = sum(1 for b in beds if b["adm"])
            wards.append({"ward": ward, "beds": beds, "occupied": n_occ, "total": len(beds)})
            total += len(beds)
            occupied += n_occ
        return render(
            request,
            "wards/board.html",
            {
                "wards": wards,
                "total": total,
                "occupied": occupied,
                "free": total - occupied,
                "can_release": request.user.has_role(*NURSE),
                "can_open": request.user.has_role(*CARE),
            },
        )


class BedReadyView(RoleRequiredMixin, View):
    allowed_roles = NURSE
    http_method_names = ["post"]

    def post(self, request, bed_pk):
        bed = get_object_or_404(Bed, pk=bed_pk)
        if bed.status == Bed.Status.CLEANING:
            bed.status = Bed.Status.FREE
            bed.save()
            messages.success(request, f"{bed} is ready for the next patient.")
        return redirect("wards:board")


class AdmitView(RoleRequiredMixin, View):
    allowed_roles = DOCTOR

    def _subject(self, visit_pk, patient_pk):
        if visit_pk:
            visit = get_object_or_404(Visit.objects.select_related("patient"), pk=visit_pk)
            return visit, visit.patient
        return None, get_object_or_404(Patient, pk=patient_pk)

    def _page(self, request, visit, patient, form):
        return render(request, "wards/admit.html", {"visit": visit, "patient": patient, "form": form})

    def get(self, request, visit_pk=None, patient_pk=None):
        visit, patient = self._subject(visit_pk, patient_pk)
        if Admission.objects.filter(patient=patient, status="admitted").exists():
            messages.error(request, f"{patient.full_name} is already admitted.")
            return redirect("wards:board")
        if visit and not visit.is_open:
            messages.error(request, "That visit is closed.")
            return redirect("queue")
        reason = ", ".join(str(d.icd10) for d in visit.diagnoses.all()) if visit else ""
        return self._page(request, visit, patient, AdmitForm(initial={"reason": reason}))

    def post(self, request, visit_pk=None, patient_pk=None):
        visit, patient = self._subject(visit_pk, patient_pk)
        form = AdmitForm(request.POST)
        if not form.is_valid():
            return self._page(request, visit, patient, form)
        try:
            with transaction.atomic():
                if visit is None:
                    visit = Visit.objects.create(patient=patient, kind="inpatient", doctor=request.user)
                visit.kind, visit.outcome, visit.doctor = "inpatient", Outcome.ADMIT, request.user
                visit.finished_at = timezone.now()
                visit.save()
                bed = form.cleaned_data["bed"]
                adm = Admission.objects.create(
                    patient=patient,
                    visit=visit,
                    bed=bed,
                    admitted_by=request.user,
                    reason=form.cleaned_data["reason"],
                )
                BedMove.objects.create(admission=adm, from_bed=None, to_bed=bed, moved_by=request.user)
                bed.status = Bed.Status.OCCUPIED
                bed.save()
                if visit.queue_entry:
                    visit.queue_entry.apply("admit", request.user)
        except TransitionError as e:
            messages.error(request, str(e))
            return self._page(request, visit, patient, AdmitForm(request.POST))
        except IntegrityError:
            messages.error(request, "That bed was just taken, or the patient is already admitted. Choose again.")
            return self._page(request, visit, patient, AdmitForm(request.POST))
        messages.success(request, f"{patient.full_name} admitted to {adm.bed}.")
        return redirect("wards:admission", pk=adm.pk)


class AdmissionView(RoleRequiredMixin, View):
    allowed_roles = CARE

    def get(self, request, pk):
        adm = get_object_or_404(Admission.objects.select_related("patient", "visit"), pk=pk)
        log_access(adm.visit)
        return render_visit(request, adm.visit)


class TransferView(VisitPostView):
    """Nurses move patients between beds."""

    allowed_roles = NURSE

    def act(self, request):
        adm = get_admission(self.visit)
        form = TransferForm(request.POST)
        if not adm or adm.status != "admitted" or not form.is_valid():
            messages.error(request, "Choose a free bed to move to.")
            return redirect(self.back)
        new_bed, old_bed = form.cleaned_data["bed"], adm.bed
        try:
            with transaction.atomic():
                adm.bed = new_bed
                adm.save()
                BedMove.objects.create(admission=adm, from_bed=old_bed, to_bed=new_bed, moved_by=request.user)
                old_bed.status = Bed.Status.CLEANING
                old_bed.save()
                new_bed.status = Bed.Status.OCCUPIED
                new_bed.save()
        except IntegrityError:
            messages.error(request, "That bed was just taken. Choose another.")
            return redirect(self.back)
        messages.success(request, f"Moved to {new_bed}.")
        return redirect(self.back)


class DischargeView(VisitPostView):
    def act(self, request):
        adm = get_admission(self.visit)
        if not adm or adm.status != "admitted":
            raise Http404
        if not self.visit.diagnoses.exists():
            messages.error(request, "Add at least one diagnosis before discharge.")
            return redirect(self.back)
        form = DischargeForm(request.POST)
        if not form.is_valid():
            return self.invalid(request, discharge_form=form)
        d = form.cleaned_data
        with transaction.atomic():
            adm.status = "discharged"
            adm.discharged_at, adm.discharged_by = timezone.now(), request.user
            adm.condition_at_discharge = d["condition_at_discharge"]
            adm.summary, adm.follow_up = d["summary"], d["follow_up"]
            adm.discharge_medication = d["discharge_medication"]
            adm.save()
            adm.bed.status = Bed.Status.CLEANING
            adm.bed.save()
            self.visit.close()
            if self.visit.queue_entry:
                try:
                    self.visit.queue_entry.apply("discharge", request.user)
                except TransitionError as e:
                    messages.error(request, str(e))
                    raise
        if self.visit.orders.filter(status__in=["ordered", "in_progress"]).exists():
            messages.warning(request, "Some tests were still pending at discharge.")
        messages.success(request, f"{adm.patient.full_name} discharged. The bed is being cleaned.")
        return redirect("wards:summary", pk=adm.pk)


class SummaryView(RoleRequiredMixin, View):
    allowed_roles = CARE

    def get(self, request, pk):
        adm = get_object_or_404(Admission.objects.select_related("patient", "visit", "bed__ward"), pk=pk)
        from apps.core.audit import log_event

        log_event("print", adm.visit)
        visit = adm.visit
        orders = list(visit.orders.exclude(status="cancelled").select_related("test"))
        for o in orders:
            o.result = o.current_result
        return render(
            request,
            "wards/summary.html",
            {
                "adm": adm,
                "visit": visit,
                "patient": adm.patient,
                "diagnoses": visit.diagnoses.select_related("icd10"),
                "orders": orders,
                "prescriptions": visit.prescriptions.exclude(status="cancelled").select_related("drug"),
                "moves": adm.moves.select_related("from_bed__ward", "to_bed__ward"),
            },
        )
