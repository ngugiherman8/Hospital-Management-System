import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core.audit import log_access
from apps.core.permissions import role_required
from apps.patients.models import Patient

from .forms import ContactForm, DeliveryForm, PostnatalForm, PregnancyForm
from .models import DeliverySummary, PregnancyEpisode, record_delivery

# Pregnancy records can hold HIV status and similar results, so nurses and other staff are left out.
MATERNITY = (Role.MIDWIFE, Role.DOCTOR)


@role_required(*MATERNITY)
def pregnancy_list(request):
    episodes = PregnancyEpisode.objects.filter(status="active").select_related("patient").prefetch_related("contacts")
    rows = [(e, e.next_contact()) for e in episodes]
    return render(request, "maternity/list.html", {"rows": rows})


@role_required(*MATERNITY)
def due_list(request):
    today = timezone.localdate()
    antenatal = []
    for episode in PregnancyEpisode.objects.filter(status="active").select_related("patient"):
        nxt = episode.next_contact(today)
        if nxt and nxt[2] in ("due", "missed"):
            antenatal.append((episode, *nxt))
    postnatal = []
    deliveries = DeliverySummary.objects.select_related("episode__patient", "baby")
    for delivery in deliveries.filter(delivered_at__gte=timezone.now() - datetime.timedelta(days=90)):
        for who, person, row, due, status in delivery.pending_contacts(today):
            if status in ("due", "missed"):
                postnatal.append((delivery, who, person, row, due, status))
    antenatal.sort(key=lambda r: (r[3] != "missed", r[2]))
    postnatal.sort(key=lambda r: (r[5] != "missed", r[4]))
    return render(
        request,
        "maternity/due.html",
        {"antenatal": antenatal, "postnatal": postnatal, "can_record": request.user.has_role(Role.MIDWIFE)},
    )


@role_required(*MATERNITY)
def for_patient(request, patient_pk):
    """The patient's active (or latest) pregnancy, or the screen to open one."""
    patient = get_object_or_404(Patient, pk=patient_pk)
    episode = patient.pregnancies.filter(status="active").first() or patient.pregnancies.first()
    if episode:
        return redirect("pregnancy_detail", pk=episode.pk)
    return redirect("pregnancy_start", patient_pk=patient.pk)


@role_required(*MATERNITY)
def pregnancy_start(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    if patient.sex != "F":
        messages.error(request, "A pregnancy can only be opened for a female patient.")
        return redirect("patient_detail", pk=patient.pk)
    existing = patient.pregnancies.filter(status="active").first()
    if existing:
        return redirect("pregnancy_detail", pk=existing.pk)
    form = PregnancyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        episode = form.save(commit=False)
        episode.patient = patient
        try:
            episode.save()
        except IntegrityError:
            messages.error(request, "This patient already has an active pregnancy.")
            return redirect("patient_detail", pk=patient.pk)
        messages.success(request, f"Pregnancy opened. Due {episode.edd:%d %b %Y}.")
        return redirect("pregnancy_detail", pk=episode.pk)
    return render(request, "maternity/start.html", {"form": form, "patient": patient})


@role_required(*MATERNITY)
def pregnancy_detail(request, pk):
    episode = get_object_or_404(PregnancyEpisode.objects.select_related("patient"), pk=pk)
    log_access(episode)
    delivery = getattr(episode, "delivery", None)
    contacts = list(episode.contacts.all())
    for contact in contacts:
        log_access(contact)
    ctx = {
        "episode": episode,
        "patient": episode.patient,
        "contacts": contacts,
        "next": episode.next_contact() if episode.is_active else None,
        "next_visit": episode.next_visit() if episode.is_active else None,
        "delivery": delivery,
        "can_edit": True,
        "contact_form": ContactForm(),
        "delivery_form": DeliveryForm(),
    }
    if delivery:
        log_access(delivery)
        ctx["postnatal"] = delivery.postnatal_contacts.select_related("patient")
        ctx["pending"] = delivery.pending_contacts()
        ctx["postnatal_form"] = PostnatalForm()
    return render(request, "maternity/detail.html", ctx)


@role_required(*MATERNITY)
@require_POST
def contact_add(request, pk):
    episode = get_object_or_404(PregnancyEpisode, pk=pk)
    if not episode.is_active:
        messages.error(request, "This pregnancy is closed.")
        return redirect("pregnancy_detail", pk=pk)
    form = ContactForm(request.POST)
    if form.is_valid():
        contact = form.save(commit=False)
        contact.episode = episode
        contact.recorded_by = request.user
        contact.save()
        flag = (
            " Danger sign or high blood pressure: the woman is flagged for doctor review."
            if contact.needs_review
            else ""
        )
        messages.success(request, f"Antenatal contact {contact.number} saved ({contact.weeks} weeks).{flag}")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("pregnancy_detail", pk=pk)


@role_required(*MATERNITY)
@require_POST
def edd_update(request, pk):
    episode = get_object_or_404(PregnancyEpisode, pk=pk)
    form = PregnancyForm(request.POST, instance=episode)
    if form.is_valid():
        form.save()
        messages.success(request, "Pregnancy details updated.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("pregnancy_detail", pk=pk)


@role_required(*MATERNITY)
@require_POST
def delivery_add(request, pk):
    episode = get_object_or_404(PregnancyEpisode, pk=pk)
    form = DeliveryForm(request.POST)
    if form.is_valid():
        data = dict(form.cleaned_data)
        sex, name = data.pop("baby_sex"), data.pop("baby_name")
        try:
            delivery = record_delivery(episode, request.user, baby_sex=sex, baby_name=name, **data)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            baby = f" Baby record {delivery.baby.hospital_number} created." if delivery.baby else ""
            messages.success(request, f"Delivery recorded.{baby}")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("pregnancy_detail", pk=pk)


@role_required(*MATERNITY)
@require_POST
def postnatal_add(request, pk, who):
    delivery = get_object_or_404(DeliverySummary.objects.select_related("episode__patient", "baby"), pk=pk)
    person = delivery.episode.patient if who == "mother" else delivery.baby
    if who not in ("mother", "baby") or person is None:
        messages.error(request, "There is no baby record for this delivery.")
        return redirect("pregnancy_detail", pk=delivery.episode_id)
    form = PostnatalForm(request.POST)
    if form.is_valid():
        contact = form.save(commit=False)
        contact.delivery = delivery
        contact.who = who
        contact.patient = person
        contact.recorded_by = request.user
        contact.save()
        messages.success(request, f"Postnatal contact {contact.number} saved for the {who}.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("pregnancy_detail", pk=delivery.episode_id)
