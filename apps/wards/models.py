from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.core.audit import AuditedModel
from apps.core.models import Sequence


class Ward(models.Model):
    class WardType(models.TextChoices):
        GENERAL = "general", "General"
        MATERNITY = "maternity", "Maternity"
        PAEDIATRIC = "paediatric", "Paediatric"
        PRIVATE = "private", "Private"

    name = models.CharField(max_length=60, unique=True)
    ward_type = models.CharField(max_length=12, choices=WardType.choices, default=WardType.GENERAL)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Bed(models.Model):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        OCCUPIED = "occupied", "Occupied"
        CLEANING = "cleaning", "Being cleaned"

    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name="beds")
    number = models.CharField(max_length=10)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FREE, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ward__name", "number"]
        unique_together = [("ward", "number")]

    def __str__(self):
        return f"{self.ward.name} bed {self.number}"


class Admission(AuditedModel):
    class Status(models.TextChoices):
        ADMITTED = "admitted", "In ward"
        DISCHARGED = "discharged", "Discharged"

    class Condition(models.TextChoices):
        IMPROVED = "improved", "Improved"
        STABLE = "stable", "Stable"
        UNCHANGED = "unchanged", "Unchanged"
        REFERRED = "referred", "Referred"
        DECEASED = "deceased", "Deceased"

    number = models.CharField(max_length=24, unique=True, editable=False)
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="admissions")
    visit = models.OneToOneField("clinical.Visit", on_delete=models.PROTECT, related_name="admission")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="admissions")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ADMITTED, db_index=True)
    admitted_at = models.DateTimeField(default=timezone.now)
    admitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.TextField()

    discharged_at = models.DateTimeField(null=True, blank=True)
    discharged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    condition_at_discharge = models.CharField(max_length=10, choices=Condition.choices, blank=True)
    summary = models.TextField(blank=True, help_text="Course in hospital and treatment given")
    discharge_medication = models.TextField(blank=True)
    follow_up = models.TextField(blank=True)

    class Meta:
        ordering = ["-admitted_at"]
        constraints = [
            # The database, not just the form, guarantees one active admission per bed and per patient.
            models.UniqueConstraint(fields=["bed"], condition=models.Q(status="admitted"), name="one_patient_per_bed"),
            models.UniqueConstraint(
                fields=["patient"], condition=models.Q(status="admitted"), name="one_active_admission_per_patient"
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not self.number:
            with transaction.atomic():
                self.number = Sequence.next_number("ADM")
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    @property
    def days_in_ward(self):
        end = self.discharged_at or timezone.now()
        return max((end - self.admitted_at).days, 0) + 1

    def __str__(self):
        return f"{self.number} {self.patient.full_name} in {self.bed}"


class BedMove(AuditedModel):
    """Every placement and transfer of an admission. The first row has no 'from' bed."""

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="moves")
    from_bed = models.ForeignKey(Bed, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="+")
    moved_at = models.DateTimeField(default=timezone.now)
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["moved_at", "id"]

    def __str__(self):
        return f"{self.from_bed or 'admitted'} to {self.to_bed}"
