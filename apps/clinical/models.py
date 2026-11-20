from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.appointments.models import Appointment
from apps.core.models import AuditedModel

# Proposed normal ranges (low, high) for flagging. A clinical lead must sign these off before go-live.
ADULT_RANGES = {
    "temperature": (Decimal("36.0"), Decimal("37.5")),
    "pulse": (60, 100),
    "resp_rate": (12, 20),
    "bp_systolic": (90, 140),
    "bp_diastolic": (60, 90),
    "spo2": (95, 100),
}
CHILD_RANGES = {  # under 12 years
    "temperature": (Decimal("36.0"), Decimal("37.5")),
    "pulse": (70, 120),
    "resp_rate": (18, 30),
    "bp_systolic": (80, 120),
    "bp_diastolic": (50, 80),
    "spo2": (95, 100),
}
VITAL_LABELS = {
    "temperature": "Temperature",
    "pulse": "Pulse",
    "resp_rate": "Breathing rate",
    "bp_systolic": "BP systolic",
    "bp_diastolic": "BP diastolic",
    "spo2": "SpO2",
}


class TriageRecord(AuditedModel):
    class Priority(models.TextChoices):
        EMERGENCY = "emergency", "Emergency"
        URGENT = "urgent", "Urgent"
        ROUTINE = "routine", "Routine"

    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="triage")
    nurse = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    temperature = models.DecimalField("Temperature (°C)", max_digits=4, decimal_places=1, null=True, blank=True)
    pulse = models.PositiveSmallIntegerField("Pulse (per min)", null=True, blank=True)
    resp_rate = models.PositiveSmallIntegerField("Breathing rate (per min)", null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField("BP systolic", null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField("BP diastolic", null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField("SpO2 (%)", null=True, blank=True)
    weight_kg = models.DecimalField("Weight (kg)", max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField("Height (cm)", max_digits=4, decimal_places=1, null=True, blank=True)
    pain = models.PositiveSmallIntegerField("Pain (0-10)", null=True, blank=True)
    complaint = models.TextField("Main complaint")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)

    def __str__(self):
        return f"Triage for {self.appointment}"

    def clean(self):
        if self.pain is not None and not 0 <= self.pain <= 10:
            raise ValidationError({"pain": "Pain is scored from 0 to 10."})
        if self.spo2 is not None and self.spo2 > 100:
            raise ValidationError({"spo2": "SpO2 cannot be above 100."})

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm:
            metres = self.height_cm / 100
            return (self.weight_kg / (metres * metres)).quantize(Decimal("0.1"))
        return None

    def flags(self):
        """Names of vitals that are outside the proposed range for the patient's age."""
        ranges = CHILD_RANGES if self.appointment.patient.age < 12 else ADULT_RANGES
        out = []
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if value is not None and not low <= value <= high:
                out.append(name)
        return out

    @property
    def flag_labels(self):
        return [VITAL_LABELS[n] for n in self.flags()]


class Consultation(AuditedModel):
    class Outcome(models.TextChoices):
        PENDING = "", "Not decided"
        HOME = "home", "Go home"
        ADMIT = "admit", "Admit"
        REFER = "refer", "Refer"

    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="consultation")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    outcome = models.CharField(max_length=10, choices=Outcome.choices, blank=True, default="")
    refer_to = models.CharField("Refer to", max_length=200, blank=True)
    review_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Consultation for {self.appointment}"

    @property
    def current_note(self):
        return self.notes.first()


class NoteVersion(AuditedModel):
    """One saved version of the clinical note. Never edited or deleted: a change is a new version."""

    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT, related_name="notes")
    version = models.PositiveIntegerField(editable=False)
    history = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    plan = models.TextField(blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["consultation", "version"], name="unique_note_version")]

    def __str__(self):
        return f"Note v{self.version}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Saved notes cannot be changed; add a new version.")
        if not self.version:
            last = NoteVersion.objects.filter(consultation=self.consultation).order_by("-version").first()
            self.version = (last.version if last else 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Saved notes cannot be deleted.")


class Diagnosis(AuditedModel):
    class Certainty(models.TextChoices):
        PROVISIONAL = "provisional", "Provisional"
        CONFIRMED = "confirmed", "Confirmed"

    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT, related_name="diagnoses")
    icd10_code = models.CharField("ICD-10 code", max_length=10, blank=True)
    name = models.CharField(max_length=200)
    certainty = models.CharField(max_length=12, choices=Certainty.choices, default=Certainty.PROVISIONAL)

    class Meta:
        verbose_name_plural = "diagnoses"
        ordering = ["id"]

    def __str__(self):
        return f"{self.icd10_code} {self.name}".strip()
