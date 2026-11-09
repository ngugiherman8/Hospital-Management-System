import re

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.appointments.models import Clinic, QueueEntry
from apps.core.audit import log_event
from apps.core.permissions import FRONT_DESK, FRONT_DESK_AND_CLINICAL, RoleRequiredMixin

from .duplicates import find_duplicates
from .forms import PatientForm
from .models import Patient


def phone_search_q(q):
    """Match a typed phone number (0712..., +254712..., 712...) against the normalised column."""
    if not re.fullmatch(r"[\d\s+\-]+", q):
        return Q(pk__in=[])
    digits = re.sub(r"\D", "", q)
    digits = digits[3:] if digits.startswith("254") else digits.lstrip("0")
    return Q(phone_normalized__contains=digits) if digits else Q(pk__in=[])


def _dupes_for(form):
    d = form.cleaned_data if hasattr(form, "cleaned_data") else {}
    raw = form.data
    return find_duplicates(
        first_name=raw.get("first_name", ""),
        last_name=raw.get("last_name", ""),
        date_of_birth=d.get("date_of_birth"),
        phone=raw.get("phone", ""),
        id_type=raw.get("id_type", ""),
        id_number=raw.get("id_number", ""),
        exclude_pk=form.instance.pk,
    )


class PatientListView(RoleRequiredMixin, ListView):
    allowed_roles = FRONT_DESK_AND_CLINICAL + ("lab", "pharmacist", "cashier")
    template_name = "patients/list.html"
    paginate_by = 25
    context_object_name = "patients"

    def get_queryset(self):
        q = self.request.GET.get("q", "").strip()
        qs = Patient.objects.filter(is_active=True)
        if q:
            cond = (
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(hospital_number__icontains=q)
                | Q(phone__icontains=q)
                | Q(id_number__icontains=q)
            )
            cond |= phone_search_q(q)
            qs = qs.filter(cond)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx

    def get_template_names(self):
        if self.request.htmx:
            return ["patients/_rows.html"]
        return [self.template_name]


class PatientCreateView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK
    template_name = "patients/form.html"

    def _ctx(self, form, dupes=None):
        return {
            "form": form,
            "dupes": dupes or [],
            "clinics": Clinic.objects.filter(is_active=True),
            "doctors": _doctors(),
            "title": "Register patient",
        }

    def get(self, request):
        return render(request, self.template_name, self._ctx(PatientForm()))

    def post(self, request):
        form = PatientForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._ctx(form))
        dupes = _dupes_for(form)
        if dupes and not request.POST.get("confirm_new"):
            # Stop and show possible matches; the receptionist decides.
            return render(request, self.template_name, self._ctx(form, dupes))
        with transaction.atomic():
            patient = form.save()
            form.save_cover(patient)
            if request.POST.get("add_to_queue"):
                clinic = get_object_or_404(Clinic, pk=request.POST.get("clinic"))
                doctor = _doctors().filter(pk=request.POST.get("doctor") or None).first()
                QueueEntry.join(patient=patient, clinic=clinic, doctor=doctor)
        messages.success(request, f"{patient.full_name} registered as {patient.hospital_number}.")
        return redirect("queue" if request.POST.get("add_to_queue") else patient.get_absolute_url())


class PatientDetailView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK_AND_CLINICAL + ("lab", "pharmacist", "cashier")

    def get(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        log_event("view", patient)  # every view of a record is audited
        return render(
            request,
            "patients/detail.html",
            {
                "patient": patient,
                "cover": patient.covers.first(),
                "appointments": patient.appointments.select_related("clinic", "doctor")[:10],
                "clinics": Clinic.objects.filter(is_active=True),
                "doctors": _doctors(),
            },
        )


class PatientEditView(RoleRequiredMixin, View):
    allowed_roles = FRONT_DESK

    def get(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        return render(
            request,
            "patients/form.html",
            {
                "form": PatientForm(instance=patient),
                "dupes": [],
                "title": f"Edit {patient.full_name}",
                "editing": True,
                "patient": patient,
            },
        )

    def post(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        form = PatientForm(request.POST, instance=patient)
        if not form.is_valid():
            return render(
                request,
                "patients/form.html",
                {"form": form, "dupes": [], "title": f"Edit {patient.full_name}", "editing": True, "patient": patient},
            )
        with transaction.atomic():
            form.save()
            form.save_cover(patient)
        messages.success(request, "Patient details updated.")
        return redirect(patient.get_absolute_url())


def _doctors():
    from apps.core.models import User

    return User.objects.filter(role="doctor", is_active=True).order_by("first_name", "last_name")
