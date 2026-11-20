from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.clinical.models import Consultation
from apps.core.models import AuditedModel
from apps.patients.models import Patient

PERMANENT_TEETH = [q * 10 + n for q in (1, 2, 3, 4) for n in range(1, 9)]  # 11-18, 21-28, 31-38, 41-48
BABY_TEETH = [q * 10 + n for q in (5, 6, 7, 8) for n in range(1, 6)]  # 51-55, 61-65, 71-75, 81-85
VALID_TEETH = set(PERMANENT_TEETH + BABY_TEETH)


def validate_fdi(value):
    if value not in VALID_TEETH:
        raise ValidationError(f"{value} is not a valid FDI tooth number (11-48 for adult teeth, 51-85 for baby teeth).")


class DentalProcedure(AuditedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    default_price = models.DecimalField(
        "Estimate (placeholder)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Prices per payer arrive in Phase 3.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DentalVisit(AuditedModel):
    consultation = models.OneToOneField(Consultation, on_delete=models.PROTECT, related_name="dental")
    dentist = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    complaint = models.CharField(max_length=300, blank=True)
    dental_history = models.TextField(blank=True)
    allergies_note = models.CharField("Allergies (including anaesthetic)", max_length=200, blank=True)
    has_diabetes = models.BooleanField("Diabetes", default=False)
    bleeding_problems = models.BooleanField("Bleeding problems", default=False)
    current_medicines = models.CharField(max_length=300, blank=True)
    examination = models.TextField(blank=True)

    def __str__(self):
        return f"Dental visit for {self.consultation.appointment}"

    @property
    def patient(self):
        return self.consultation.appointment.patient


class ToothRecord(AuditedModel):
    """One entry on the tooth chart. Append only: the chart at an earlier date stays readable."""

    class Surface(models.TextChoices):
        WHOLE = "whole", "Whole tooth"
        MESIAL = "M", "Mesial"
        DISTAL = "D", "Distal"
        OCCLUSAL = "O", "Occlusal or incisal"
        BUCCAL = "B", "Buccal or labial"
        LINGUAL = "L", "Lingual or palatal"

    class Condition(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        DECAYED = "decayed", "Decayed"
        FILLED = "filled", "Filled"
        MISSING = "missing", "Missing"
        EXTRACTED = "extracted", "Extracted today"
        CROWNED = "crowned", "Crowned"
        ROOT_FILLED = "root_filled", "Root canal filled"
        FRACTURED = "fractured", "Fractured"
        UNERUPTED = "unerupted", "Not yet erupted"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="tooth_records")
    visit = models.ForeignKey(DentalVisit, on_delete=models.PROTECT, related_name="tooth_records")
    tooth = models.PositiveSmallIntegerField(validators=[validate_fdi])
    surface = models.CharField(max_length=5, choices=Surface.choices, default=Surface.WHOLE)
    condition = models.CharField(max_length=12, choices=Condition.choices)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["patient", "tooth"])]

    def __str__(self):
        return f"Tooth {self.tooth} {self.surface}: {self.condition}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Tooth records cannot be changed; add a new entry.")
        validate_fdi(self.tooth)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Tooth records cannot be deleted.")


def current_chart(patient):
    """tooth -> list of (surface label, condition label) using the latest entry for each tooth surface."""
    latest = {}
    for record in ToothRecord.objects.filter(patient=patient).order_by("id"):
        latest[(record.tooth, record.surface)] = record
    chart = {}
    for (tooth, _surface), record in latest.items():
        if record.condition == ToothRecord.Condition.HEALTHY:
            continue
        chart.setdefault(tooth, []).append(
            (record.get_surface_display(), record.get_condition_display(), record.condition)
        )
    return chart


class TreatmentPlan(AuditedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="treatment_plans")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    estimate_shown_at = models.DateTimeField(null=True, blank=True, editable=False)
    consent_by = models.CharField("Agreed by (patient or payer)", max_length=150, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True, editable=False)
    consent_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Treatment plan {self.pk} for {self.patient.full_name}"

    @property
    def total_estimate(self):
        return sum((i.estimate for i in self.items.exclude(status="declined")), Decimal("0"))

    @property
    def has_consent(self):
        return self.consent_at is not None

    def record_consent(self, user, agreed_by):
        """The estimate was shown to the patient or payer and they agreed. Needed before any work starts."""
        if not agreed_by.strip():
            raise ValidationError("Write the name of the person who agreed.")
        if not self.items.exclude(status="declined").exists():
            raise ValidationError("Add at least one item to the plan first.")
        now = timezone.now()
        self.estimate_shown_at = now
        self.consent_by = agreed_by.strip()
        self.consent_at = now
        self.consent_recorded_by = user
        self.save()

    def close_if_finished(self):
        if self.status == self.Status.OPEN and not self.items.filter(status__in=("planned", "in_progress")).exists():
            self.status = self.Status.CLOSED
            self.save()


class TreatmentPlanItem(AuditedModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"
        DECLINED = "declined", "Declined"

    plan = models.ForeignKey(TreatmentPlan, on_delete=models.PROTECT, related_name="items")
    tooth = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[validate_fdi], help_text="Leave empty for the whole mouth."
    )
    procedure = models.ForeignKey(DentalProcedure, on_delete=models.PROTECT, related_name="+")
    estimate = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED)
    anaesthetic_note = models.CharField(max_length=200, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    completed_visit = models.ForeignKey(
        DentalVisit, null=True, blank=True, on_delete=models.PROTECT, related_name="procedures_done", editable=False
    )
    billed = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        where = f"tooth {self.tooth}" if self.tooth else "whole mouth"
        return f"{self.procedure.name}, {where}"

    def clean(self):
        if self.tooth is not None:
            validate_fdi(self.tooth)

    def save(self, *args, **kwargs):
        if self.status == self.Status.DONE and not self.procedure_id:
            raise ValidationError("A plan item cannot be marked done without a procedure.")
        if self.tooth is not None:
            validate_fdi(self.tooth)
        super().save(*args, **kwargs)

    @property
    def ready_for_billing(self):
        return self.status == self.Status.DONE and not self.billed

    def _need_consent(self):
        if not self.plan.has_consent:
            raise ValidationError("Show the estimate and record the patient's consent before starting work.")

    def start(self, user):
        if self.status != self.Status.PLANNED:
            raise ValidationError("Only a planned item can be started.")
        self._need_consent()
        self.status = self.Status.IN_PROGRESS
        self.save()

    def complete(self, user, visit, anaesthetic_note=""):
        if self.status not in (self.Status.PLANNED, self.Status.IN_PROGRESS):
            raise ValidationError("This item is already finished.")
        self._need_consent()
        with transaction.atomic():
            self.status = self.Status.DONE
            self.completed_at = timezone.now()
            self.completed_by = user
            self.completed_visit = visit
            self.anaesthetic_note = anaesthetic_note
            self.save()
            self.plan.close_if_finished()

    def decline(self):
        if self.status in (self.Status.DONE, self.Status.DECLINED):
            raise ValidationError("This item is already finished.")
        self.status = self.Status.DECLINED
        self.save()
        self.plan.close_if_finished()
