import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.clinical.models import Consultation
from apps.core.models import AuditedModel


class Drug(AuditedModel):
    generic_name = models.CharField(max_length=150)
    strength = models.CharField(max_length=60, blank=True)
    form = models.CharField(max_length=60, blank=True, help_text="Tablet, syrup, injection...")
    route = models.CharField(max_length=40, blank=True, default="Oral")
    price = models.DecimalField("Price (placeholder)", max_digits=10, decimal_places=2, default=0)
    reorder_level = models.PositiveIntegerField(
        default=20, help_text="Warn the pharmacy when stock falls to this many units (placeholder until counted)."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["generic_name", "strength"]
        constraints = [models.UniqueConstraint(fields=["generic_name", "strength", "form"], name="unique_drug_entry")]

    def __str__(self):
        return " ".join(p for p in (self.generic_name, self.strength, self.form) if p)

    def matches_allergy(self, allergies):
        """True when the patient's allergy text names this drug (any word of its generic name, 4+ letters)."""
        words = {w for w in re.split(r"[^a-z]+", (allergies or "").lower()) if len(w) >= 4}
        names = {w for w in re.split(r"[^a-z]+", self.generic_name.lower()) if len(w) >= 4}
        return bool(words & names)


class Prescription(AuditedModel):
    class Dispensing(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Part dispensed"
        DISPENSED = "dispensed", "Dispensed"

    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT, related_name="prescriptions")
    prescribed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    dispensing_status = models.CharField(
        max_length=10, db_index=True, choices=Dispensing.choices, default=Dispensing.PENDING
    )

    def __str__(self):
        return f"Prescription {self.pk} for {self.consultation}"


class PrescriptionItem(AuditedModel):
    prescription = models.ForeignKey(Prescription, on_delete=models.PROTECT, related_name="items")
    drug = models.ForeignKey(Drug, on_delete=models.PROTECT, related_name="+")
    dose = models.CharField(max_length=80)
    frequency = models.CharField(max_length=80)
    days = models.PositiveSmallIntegerField()
    quantity = models.PositiveIntegerField()
    allergy_warning = models.BooleanField(default=False, editable=False)
    allergy_acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    allergy_acknowledged_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.drug}: {self.dose}, {self.frequency}, {self.days} days"

    def save(self, *args, **kwargs):
        patient = self.prescription.consultation.appointment.patient
        self.allergy_warning = self.drug.matches_allergy(patient.allergies)
        if self.allergy_warning and not self.allergy_acknowledged_by_id:
            raise ValidationError("This patient has a recorded allergy to this drug. Acknowledge the warning to save.")
        if self.allergy_acknowledged_by_id and not self.allergy_acknowledged_at:
            self.allergy_acknowledged_at = timezone.now()
        super().save(*args, **kwargs)
