"""Turn clinical events into draft charges. Nothing here edits a clinical record, and a failure to price
something never blocks care: it raises a ChargeAlert for the cashier instead."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.appointments.models import Appointment, Status
from apps.dental.models import TreatmentPlanItem
from apps.imaging.models import ImagingStudy
from apps.maternity.models import AntenatalContact, PostnatalContact
from apps.orders.models import OrderItem
from apps.wards.models import Admission

from .services import charge_bed_nights, post_charge, service_by_code, withdraw_charge

NOT_ARRIVED = (Status.BOOKED, Status.CANCELLED, Status.NO_SHOW)
CONSULTATION_DONE = (Status.COMPLETED, Status.ADMITTED)
CONSULTATION_CODES = {"general": "CONS-GENERAL", "dental": "CONS-DENTAL", "mch": "CONS-MCH", "eye": "CONS-EYE"}


@receiver(post_save, sender=Appointment)
def appointment_charges(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if instance.status not in NOT_ARRIVED:
        # Registration is charged once per patient, on their first visit.
        post_charge(
            instance.patient,
            service_by_code("REG"),
            appointment=instance,
            source_key=f"reg:{instance.patient_id}",
        )
    if instance.status in CONSULTATION_DONE:
        code = CONSULTATION_CODES.get(instance.clinic, "CONS-GENERAL")
        post_charge(
            instance.patient,
            service_by_code(code) or service_by_code("CONS-GENERAL"),
            appointment=instance,
            source_key=f"cons:{instance.pk}",
        )


@receiver(post_save, sender=OrderItem)
def order_item_charges(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    key = f"test:{instance.pk}"
    if instance.status == OrderItem.Status.CANCELLED:
        withdraw_charge(key, "Test cancelled")
        return
    # Lab tests are charged when ordered. Scans are charged when the images are done (see below).
    if created and instance.order.kind == "lab":
        visit = instance.order.appointment
        post_charge(visit.patient, getattr(instance.test, "service", None), appointment=visit, source_key=key)


@receiver(post_save, sender=ImagingStudy)
def imaging_charges(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if instance.status in (ImagingStudy.Status.IMAGES_DONE, ImagingStudy.Status.PRELIMINARY, ImagingStudy.Status.FINAL):
        visit = instance.order_item.order.appointment
        post_charge(
            instance.patient,
            getattr(instance.item.test, "service", None),
            appointment=visit,
            source_key=f"test:{instance.order_item_id}",
        )


@receiver(post_save, sender=TreatmentPlanItem)
def dental_charges(sender, instance, raw=False, **kwargs):
    if raw or instance.status != TreatmentPlanItem.Status.DONE or instance.billed:
        return
    visit = instance.completed_visit.consultation.appointment if instance.completed_visit_id else None
    line = post_charge(
        instance.plan.patient,
        getattr(instance.procedure, "service", None),
        appointment=visit,
        source_key=f"dental:{instance.pk}",
        description=f"{instance.procedure.name}" + (f", tooth {instance.tooth}" if instance.tooth else ""),
    )
    if line:
        TreatmentPlanItem.objects.filter(pk=instance.pk).update(billed=True)


@receiver(post_save, sender=AntenatalContact)
def antenatal_charges(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        post_charge(
            instance.episode.patient,
            service_by_code("ANC-CONTACT"),
            source_key=f"anc:{instance.pk}",
            description=f"Antenatal contact {instance.number}",
        )


@receiver(post_save, sender=PostnatalContact)
def postnatal_charges(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        post_charge(
            instance.patient,
            service_by_code("PNC-CONTACT"),
            source_key=f"pnc:{instance.pk}",
            description=f"Postnatal contact {instance.number}",
        )


@receiver(post_save, sender=Admission)
def admission_charges(sender, instance, raw=False, **kwargs):
    if not raw and instance.discharged_at:
        charge_bed_nights(instance)
