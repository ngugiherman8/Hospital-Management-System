from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.clinical.workspace import VisitPostView
from apps.core.permissions import RoleRequiredMixin

from .forms import PrescriptionForm
from .models import Prescription


class PrescriptionAddView(VisitPostView):
    def act(self, request):
        form = PrescriptionForm(request.POST, patient=self.visit.patient, visit=self.visit)
        if not form.is_valid():
            return self.invalid(request, rx_form=form)
        d = form.cleaned_data
        warned = bool(form.allergy_conflict)
        Prescription.objects.create(
            visit=self.visit,
            drug=d["drug"],
            dose=d["dose"],
            units_per_dose=d["units_per_dose"],
            route=d["route"] or d["drug"].default_route,
            frequency=d["frequency"],
            days=d["days"],
            allergy_warning=warned,
            allergy_acknowledged_by=request.user if warned else None,
            allergy_override_reason=d["override_reason"] if warned else "",
            prescribed_by=request.user,
        )
        return redirect(self.back)


class PrescriptionCancelView(VisitPostView):
    def act(self, request, rx_pk):
        rx = get_object_or_404(Prescription, pk=rx_pk, visit=self.visit)
        if rx.status == Prescription.Status.PENDING:
            rx.status = Prescription.Status.CANCELLED
            rx.save()
        else:
            messages.error(request, "This prescription has already been dispensed.")
        return redirect(self.back)


class PrescriptionListView(RoleRequiredMixin, View):
    """Prescriptions waiting for the pharmacy. Shows only what the pharmacy needs: no notes or results."""

    allowed_roles = ("pharmacist", "doctor", "nurse")

    def get(self, request):
        waiting = (
            Prescription.objects.filter(status="pending", visit__status__in=["open", "closed"])
            .select_related("drug", "visit__patient")
            .order_by("prescribed_at")
        )
        return render(request, "pharmacy_rx/list.html", {"prescriptions": waiting})
