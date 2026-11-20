from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.models import Role
from apps.appointments.models import Appointment
from apps.core.audit import log_access
from apps.core.permissions import role_required

from .forms import PatientForm, RegistrationExtras
from .models import Patient

ALL_STAFF = (
    Role.RECEPTION, Role.NURSE, Role.DOCTOR, Role.LAB, Role.PHARMACIST, Role.CASHIER,
    Role.DENTIST, Role.MIDWIFE, Role.RADIOGRAPHER, Role.RADIOLOGIST,
)  # fmt: skip


@role_required(*ALL_STAFF)
def patient_list(request):
    query = request.GET.get("q", "")
    page = Paginator(Patient.search(query), 25).get_page(request.GET.get("page"))
    return render(request, "patients/list.html", {"page": page, "q": query})


@role_required(Role.RECEPTION)
@require_http_methods(["GET", "POST"])
def patient_create(request):
    matches = []
    if request.method == "POST":
        form = PatientForm(request.POST)
        extras = RegistrationExtras(request.POST)
        if form.is_valid() and extras.is_valid():
            data = form.cleaned_data
            matches = Patient.find_possible_duplicates(
                data["first_name"], data["last_name"], data["date_of_birth"], data["phone"], data["id_number"]
            )
            if matches and not extras.cleaned_data["confirm_new"]:
                messages.warning(request, "Possible matches found. Check them before creating a new record.")
            else:
                patient = form.save()
                if extras.cleaned_data["add_to_queue"]:
                    Appointment.objects.create(
                        patient=patient,
                        clinic=extras.cleaned_data["clinic"] or "general",
                        kind=Appointment.Kind.WALK_IN,
                        created_by=request.user,
                    )
                messages.success(request, f"{patient.full_name} registered as {patient.hospital_number}.")
                return redirect("patient_detail", pk=patient.pk)
    else:
        form = PatientForm()
        extras = RegistrationExtras()
    return render(request, "patients/form.html", {"form": form, "extras": extras, "matches": matches, "editing": False})


@role_required(Role.RECEPTION)
@require_http_methods(["GET", "POST"])
def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(request.POST or None, instance=patient)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Patient details updated.")
        return redirect("patient_detail", pk=patient.pk)
    return render(request, "patients/form.html", {"form": form, "patient": patient, "matches": [], "editing": True})


@role_required(*ALL_STAFF)
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    log_access(patient)
    visits = list(patient.appointments.select_related("doctor", "triage", "consultation")[:20])
    can_see_clinical = request.user.has_role(Role.NURSE, Role.DOCTOR, Role.DENTIST, Role.MIDWIFE)
    if can_see_clinical:
        # Opening a clinical record is itself audited.
        for visit in visits:
            for record in (getattr(visit, "triage", None), getattr(visit, "consultation", None)):
                if record is not None:
                    log_access(record)
            for order in visit.orders.prefetch_related("items__results"):
                for item in order.items.all():
                    result = item.current_result
                    if result is not None:
                        log_access(result)
    return render(
        request,
        "patients/detail.html",
        {"patient": patient, "visits": visits, "can_see_clinical": can_see_clinical},
    )
