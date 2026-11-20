from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.core.models import AuditedModel, Sequence
from apps.patients.models import Patient


class Ward(AuditedModel):
    class Kind(models.TextChoices):
        GENERAL = "general", "General"
        MATERNITY = "maternity", "Maternity"
        PAEDIATRIC = "paediatric", "Paediatric"
        PRIVATE = "private", "Private"

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.GENERAL)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Bed(AuditedModel):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        OCCUPIED = "occupied", "Occupied"
        CLEANING = "cleaning", "Cleaning"

    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name="beds")
    number = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FREE)

    class Meta:
        ordering = ["ward__name", "number"]
        constraints = [models.UniqueConstraint(fields=["ward", "number"], name="unique_bed_number_per_ward")]

    def __str__(self):
        return f"{self.ward.name} bed {self.number}"

    @property
    def current_admission(self):
        return self.admissions.filter(discharged_at__isnull=True).select_related("patient").first()


class Admission(AuditedModel):
    number = models.CharField(max_length=30, unique=True, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="admissions")
    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="admission")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="admissions")
    reason = models.CharField(max_length=300)
    admitted_at = models.DateTimeField(default=timezone.now, editable=False)
    admitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    discharged_at = models.DateTimeField(null=True, blank=True, editable=False)
    discharged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    discharge_summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-admitted_at"]
        constraints = [
            # The database, not just the form, guarantees one patient per bed and one bed per patient.
            models.UniqueConstraint(
                fields=["bed"], condition=Q(discharged_at__isnull=True), name="one_active_admission_per_bed"
            ),
            models.UniqueConstraint(
                fields=["patient"], condition=Q(discharged_at__isnull=True), name="one_active_admission_per_patient"
            ),
        ]

    def __str__(self):
        return f"{self.number} {self.patient.full_name}"

    @property
    def is_active(self):
        return self.discharged_at is None

    def save(self, *args, **kwargs):
        if not self.number:
            year = timezone.localdate().year
            self.number = f"ADM-{year}-{Sequence.next(f'admission-{year}'):06d}"
        super().save(*args, **kwargs)


class BedMove(AuditedModel):
    admission = models.ForeignKey(Admission, on_delete=models.PROTECT, related_name="moves")
    from_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="+")
    to_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="+")
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    moved_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["moved_at"]

    def __str__(self):
        return f"{self.from_bed} to {self.to_bed}"


class BedUnavailable(Exception):
    pass


def _lock_free_bed(bed_id):
    bed = Bed.objects.select_for_update().get(pk=bed_id)
    if bed.status != Bed.Status.FREE or bed.admissions.filter(discharged_at__isnull=True).exists():
        raise BedUnavailable(f"{bed} is not free.")
    return bed


def admit(appointment, bed, reason, user):
    """Admit the visit's patient to a free bed, then move the visit to Admitted."""
    from django.db import IntegrityError

    try:
        with transaction.atomic():
            bed = _lock_free_bed(bed.pk)
            admission = Admission.objects.create(
                patient=appointment.patient, appointment=appointment, bed=bed, reason=reason, admitted_by=user
            )
            bed.status = Bed.Status.OCCUPIED
            bed.save()
            appointment.refresh_from_db()
            appointment.move_to("admitted", user)
    except IntegrityError:
        raise BedUnavailable("That bed was just taken, or the patient is already admitted.") from None
    return admission


def transfer(admission, new_bed, user):
    if not admission.is_active:
        raise BedUnavailable("This patient has already been discharged.")
    try:
        with transaction.atomic():
            new_bed = _lock_free_bed(new_bed.pk)
            old_bed = Bed.objects.select_for_update().get(pk=admission.bed_id)
            BedMove.objects.create(admission=admission, from_bed=old_bed, to_bed=new_bed, moved_by=user)
            admission.bed = new_bed
            admission.save()
            old_bed.status = Bed.Status.FREE
            old_bed.save()
            new_bed.status = Bed.Status.OCCUPIED
            new_bed.save()
    except Exception as exc:  # noqa: BLE001
        from django.db import IntegrityError

        if isinstance(exc, IntegrityError):
            raise BedUnavailable("That bed was just taken.") from None
        raise
    return admission


def discharge(admission, summary, user):
    """Save the discharge summary, free the bed and complete the visit."""
    with transaction.atomic():
        admission = Admission.objects.select_for_update().get(pk=admission.pk)
        if not admission.is_active:
            raise BedUnavailable("This patient has already been discharged.")
        admission.discharged_at = timezone.now()
        admission.discharged_by = user
        admission.discharge_summary = summary
        admission.save()
        bed = Bed.objects.select_for_update().get(pk=admission.bed_id)
        bed.status = Bed.Status.FREE
        bed.save()
        visit = Appointment.objects.get(pk=admission.appointment_id)
        visit.move_to("completed", user)
    return admission
