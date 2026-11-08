from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.audit import AuditedModel


class ICD10Code(models.Model):
    code = models.CharField(max_length=10, unique=True)
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["code"]
        verbose_name = "ICD-10 code"

    def __str__(self):
        return f"{self.code} {self.title}"


class Priority(models.TextChoices):
    EMERGENCY = "emergency", "Emergency. See now"
    URGENT = "urgent", "Urgent. Within 30 minutes"
    ROUTINE = "routine", "Routine"


class Outcome(models.TextChoices):
    HOME = "home", "Go home with medicine"
    ADMIT = "admit", "Admit to ward"
    REFER = "refer", "Refer to another facility"


class Visit(AuditedModel):
    """One episode of care: triage, consultation and (if admitted) the inpatient stay all hang off this."""

    class Kind(models.TextChoices):
        OUTPATIENT = "outpatient", "Outpatient"
        INPATIENT = "inpatient", "Inpatient"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="visits")
    queue_entry = models.OneToOneField(
        "appointments.QueueEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="visit"
    )
    clinic = models.ForeignKey("appointments.Clinic", null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OUTPATIENT)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    # triage
    complaint = models.TextField(blank=True)
    duration = models.CharField(max_length=60, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    fast_track_reason = models.CharField(max_length=200, blank=True)
    triaged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    triaged_at = models.DateTimeField(null=True, blank=True)

    # consultation
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="visits"
    )
    outcome = models.CharField(max_length=8, choices=Outcome.choices, blank=True)
    review_in_days = models.PositiveSmallIntegerField(null=True, blank=True)
    referred_to = models.CharField(max_length=160, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["patient", "-started_at"])]

    @classmethod
    def for_queue_entry(cls, entry):
        visit, _ = cls.objects.get_or_create(
            queue_entry=entry, defaults={"patient": entry.patient, "clinic": entry.clinic}
        )
        return visit

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    @property
    def latest_vitals(self):
        return self.vitals.first()

    def current_note(self, kind):
        return ClinicalNote.current_for(self, kind).first()

    def close(self):
        self.status = self.Status.CLOSED
        self.closed_at = timezone.now()
        self.save()

    def __str__(self):
        return f"{self.patient.full_name} {self.started_at:%d %b %Y}"


class VitalSigns(AuditedModel):
    """A set of observations. The first set of a visit is taken at triage; ward staff add more."""

    class Context(models.TextChoices):
        TRIAGE = "triage", "Triage"
        WARD = "ward", "Ward round"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="vitals")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    context = models.CharField(max_length=8, choices=Context.choices, default=Context.TRIAGE)
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    resp_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True)
    blood_sugar = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    pain_score = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name_plural = "vital signs"

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm:
            m = Decimal(self.height_cm) / 100
            return round(Decimal(self.weight_kg) / (m * m), 1)
        return None

    @property
    def bp(self):
        if self.bp_systolic and self.bp_diastolic:
            return f"{self.bp_systolic} / {self.bp_diastolic}"
        return ""

    @property
    def alerts(self):
        """List of (label, severity) with severity 'high', 'low' or 'critical'. Adult ranges; paediatric ranges
        are an open decision (see the Phase 2 instructions)."""
        out = []
        t, p, r, s = self.temperature, self.pulse, self.resp_rate, self.spo2
        if t is not None:
            if t >= 38:
                out.append(("Temperature", "high"))
            elif t < Decimal("35.5"):
                out.append(("Temperature", "low"))
        if p is not None:
            if p > 100:
                out.append(("Pulse", "high"))
            elif p < 50:
                out.append(("Pulse", "low"))
        if r is not None and r > 20:
            out.append(("Breathing", "high"))
        if s is not None:
            if s < 90:
                out.append(("Oxygen saturation", "critical"))
            elif s < 94:
                out.append(("Oxygen saturation", "low"))
        if self.bp_systolic and self.bp_diastolic:
            if self.bp_systolic < 80:
                out.append(("Blood pressure", "critical"))
            elif self.bp_systolic >= 140 or self.bp_diastolic >= 90:
                out.append(("Blood pressure", "high"))
            elif self.bp_systolic < 90:
                out.append(("Blood pressure", "low"))
        if self.blood_sugar is not None:
            if self.blood_sugar > Decimal("11.1"):
                out.append(("Blood sugar", "high"))
            elif self.blood_sugar < Decimal("3.9"):
                out.append(("Blood sugar", "low"))
        return out

    @property
    def flagged(self):
        return {name for name, _ in self.alerts}

    @property
    def is_critical(self):
        return any(sev == "critical" for _, sev in self.alerts)

    def __str__(self):
        return f"Vitals {self.recorded_at:%d %b %H:%M}"


class VisitDiagnosis(AuditedModel):
    class Kind(models.TextChoices):
        PROVISIONAL = "provisional", "Provisional"
        CONFIRMED = "confirmed", "Confirmed"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="diagnoses")
    icd10 = models.ForeignKey(ICD10Code, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.PROVISIONAL)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["added_at"]
        unique_together = [("visit", "icd10")]
        verbose_name_plural = "diagnoses"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.icd10}"


class ClinicalNote(AuditedModel):
    """Notes are never edited or deleted. An amendment is a new version pointing at the one it replaces."""

    class Kind(models.TextChoices):
        HISTORY = "history", "History"
        EXAMINATION = "examination", "Examination"
        PLAN = "plan", "Plan"
        PROGRESS = "progress", "Progress note"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="notes")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    text = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    amends = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="amended_by")
    amend_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Clinical notes cannot be edited. Add an amendment instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Clinical notes cannot be deleted.")

    @classmethod
    def current_for(cls, visit, kind):
        """Notes of this kind that have not been replaced by an amendment, oldest first."""
        return cls.objects.filter(visit=visit, kind=kind, amended_by__isnull=True).order_by("created_at", "id")

    @property
    def history(self):
        """Earlier versions, newest first."""
        versions, node = [], self.amends
        while node:
            versions.append(node)
            node = node.amends
        return versions

    @property
    def version(self):
        return len(self.history) + 1

    def amend(self, text, author, reason=""):
        return ClinicalNote.objects.create(
            visit=self.visit, kind=self.kind, text=text, author=author, amends=self, amend_reason=reason
        )

    def __str__(self):
        return f"{self.get_kind_display()} v{self.version} {self.created_at:%d %b %H:%M}"
