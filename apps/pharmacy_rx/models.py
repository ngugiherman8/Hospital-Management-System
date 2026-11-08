from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.audit import AuditedModel


class Drug(models.Model):
    """Formulary entry. Stock batches attach to this in the pharmacy module (Phase 3)."""

    class Route(models.TextChoices):
        ORAL = "oral", "by mouth"
        IV = "iv", "intravenous"
        IM = "im", "intramuscular"
        SC = "sc", "subcutaneous"
        TOPICAL = "topical", "on the skin"
        INHALED = "inhaled", "inhaled"
        OTHER = "other", "other"

    name = models.CharField(max_length=120, help_text="Generic name, e.g. Paracetamol")
    strength = models.CharField(max_length=40, help_text="e.g. 500 mg")
    form = models.CharField(max_length=30, help_text="tablet, capsule, syrup, injection ...")
    default_route = models.CharField(max_length=10, choices=Route.choices, default=Route.ORAL)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Placeholder until Phase 3")
    allergy_keys = models.CharField(
        max_length=200,
        blank=True,
        help_text="Comma-separated words that trigger a warning if found in a patient's allergies.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "strength"]
        unique_together = [("name", "strength", "form")]

    @property
    def label(self):
        return f"{self.name} {self.strength} {self.form}"

    def allergy_conflict(self, allergies_text: str) -> str:
        """The matching allergy word, if this drug conflicts with the patient's recorded allergies."""
        text = (allergies_text or "").lower()
        keys = [self.name.lower()] + [k.strip().lower() for k in self.allergy_keys.split(",") if k.strip()]
        return next((k for k in keys if k and k in text), "")

    def __str__(self):
        return self.label


class Prescription(AuditedModel):
    class Frequency(models.TextChoices):
        STAT = "stat", "Once now"
        OD = "od", "Once a day"
        BD = "bd", "Twice a day"
        TDS = "tds", "3 times a day"
        QDS = "qds", "4 times a day"

    PER_DAY = {"stat": 1, "od": 1, "bd": 2, "tds": 3, "qds": 4}

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for pharmacy"
        DISPENSED = "dispensed", "Dispensed"
        CANCELLED = "cancelled", "Cancelled"

    visit = models.ForeignKey("clinical.Visit", on_delete=models.CASCADE, related_name="prescriptions")
    drug = models.ForeignKey(Drug, on_delete=models.PROTECT)
    dose = models.CharField(max_length=40, help_text="e.g. 1 g")
    units_per_dose = models.DecimalField(max_digits=5, decimal_places=1, default=1)
    route = models.CharField(max_length=10, choices=Drug.Route.choices, default=Drug.Route.ORAL)
    frequency = models.CharField(max_length=4, choices=Frequency.choices, default=Frequency.TDS)
    days = models.PositiveSmallIntegerField(default=3)
    quantity = models.DecimalField(max_digits=7, decimal_places=1, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    allergy_warning = models.BooleanField(default=False, help_text="The drug matched a recorded allergy")
    allergy_acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    allergy_override_reason = models.CharField(max_length=200, blank=True)
    prescribed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    prescribed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["prescribed_at", "id"]

    @staticmethod
    def compute_quantity(units_per_dose, frequency, days):
        return Decimal(units_per_dose) * Prescription.PER_DAY[frequency] * int(days)

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.quantity = self.compute_quantity(self.units_per_dose, self.frequency, self.days)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.drug.label}, {self.dose} {self.get_frequency_display()} for {self.days} days"
