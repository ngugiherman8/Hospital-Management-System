import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import AuditedModel

DEPARTMENTS = [
    ("reception", "Reception and registration"),
    ("opd", "Triage and outpatient clinic"),
    ("lab", "Laboratory"),
    ("imaging", "Imaging"),
    ("pharmacy", "Pharmacy and stock"),
    ("cashier", "Cashier and claims"),
    ("wards", "Wards"),
    ("maternity", "Antenatal, postnatal and maternity"),
    ("dental", "Dental"),
]


class LegacyRecord(AuditedModel):
    """Links a patient made by the import to the row it came from, so the import can be run again safely."""

    legacy_id = models.CharField(max_length=80, unique=True)
    legacy_number = models.CharField("Old file or card number", max_length=60, blank=True, db_index=True)
    # Several old rows can be the same person, so more than one record may point at one patient.
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="legacy_records")
    source = models.CharField(max_length=200, blank=True)
    dob_estimated = models.BooleanField(default=False, help_text="Date of birth was worked out from an age.")

    def __str__(self):
        return f"{self.legacy_id} -> {self.patient}"


class Issue(AuditedModel):
    """A problem found during the pilot or after go-live."""

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        WORKAROUND = "workaround", "Open, with a workaround"
        FIXED = "fixed", "Fixed"
        CLOSED = "closed", "Closed (checked by the reporter)"
        DEFERRED = "deferred", "Deferred"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    department = models.CharField(max_length=20, choices=DEPARTMENTS, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    workaround = models.CharField(max_length=300, blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reported_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField(null=True, blank=True, editable=False)
    resolved_on = models.DateField(null=True, blank=True, editable=False)

    # Proposed starting points. Confirm them with the hospital.
    WORKING_DAYS_TO_FIX = {"critical": 0, "high": 3}

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"

    @property
    def is_unresolved(self):
        return self.status in (self.Status.OPEN, self.Status.WORKAROUND)

    @property
    def overdue(self):
        return self.is_unresolved and self.due_on is not None and self.due_on < timezone.localdate()

    def save(self, *args, **kwargs):
        if self.due_on is None and self.severity in self.WORKING_DAYS_TO_FIX:
            self.due_on = add_working_days(self.reported_on, self.WORKING_DAYS_TO_FIX[self.severity])
        if self.status in (self.Status.FIXED, self.Status.CLOSED) and self.resolved_on is None:
            self.resolved_on = timezone.localdate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Issues are closed, not deleted.")


def add_working_days(start, days):
    day = start
    while days > 0:
        day += datetime.timedelta(days=1)
        if day.weekday() < 5:
            days -= 1
    return day


class PilotDay(AuditedModel):
    """Paper totals typed in at the end of a pilot day, set beside what the system says."""

    day = models.DateField()
    department = models.CharField(max_length=20, choices=DEPARTMENTS)
    paper_cash = models.DecimalField("Cash and M-Pesa on paper (KES)", max_digits=12, decimal_places=2, default=0)
    paper_visits = models.PositiveIntegerField("Visits on paper", default=0)
    paper_dispensed = models.PositiveIntegerField("Drugs dispensed on paper (items)", default=0)
    explanation = models.CharField("Why they differ", max_length=300, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-day", "department"]
        constraints = [models.UniqueConstraint(fields=["day", "department"], name="one_pilot_day_per_department")]

    def __str__(self):
        return f"{self.day} {self.get_department_display()}"

    def system_figures(self):
        from apps.appointments.models import Appointment, Status
        from apps.billing.models import Payment
        from apps.stock.models import StockMovement

        cash_methods = (Payment.Method.CASH, Payment.Method.MPESA)
        paid = Payment.objects.filter(created_at__date=self.day, method__in=cash_methods)
        return {
            "cash": sum((p.amount for p in paid), Decimal("0")),
            "visits": Appointment.objects.filter(visit_date=self.day)
            .exclude(status__in=(Status.CANCELLED, Status.NO_SHOW))
            .count(),
            "dispensed": StockMovement.objects.filter(
                created_at__date=self.day, kind=StockMovement.Kind.DISPENSE
            ).count(),
        }

    def comparison(self):
        system = self.system_figures()
        rows = [
            ("Cash and M-Pesa (KES)", self.paper_cash, system["cash"]),
            ("Visits", self.paper_visits, system["visits"]),
            ("Drugs dispensed (items)", self.paper_dispensed, system["dispensed"]),
        ]
        return [{"label": a, "paper": b, "system": c, "difference": c - b} for a, b, c in rows]

    @property
    def matches(self):
        return all(r["difference"] == 0 for r in self.comparison())

    @property
    def needs_explaining(self):
        return not self.matches and not self.explanation


class GoNoGo(AuditedModel):
    """One go/no-go item for one department, with the name of the person who agreed."""

    ITEMS = [
        ("signoff", "The department head and the clinical lead have signed off the pilot."),
        ("no_critical", "No critical bug is open, and every high bug has a workaround."),
        ("restore", "A backup was restored successfully within the last 30 days."),
        ("trained", "Every role has been trained, with signed attendance sheets."),
        ("forms", "Downtime paper forms are printed and placed in the department."),
        ("opening", "Opening stock and opening cash balances were counted and entered."),
        ("compliance", "Data protection registration and the compliance review are confirmed in writing."),
        ("rollback", "The rollback plan is agreed and the decision-maker is named."),
    ]

    department = models.CharField(max_length=20, choices=DEPARTMENTS)
    item = models.CharField(max_length=20, choices=[(k, v) for k, v in ITEMS])
    agreed_by = models.CharField("Name of the person who agreed", max_length=150)
    agreed_on = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["department", "item"]
        constraints = [models.UniqueConstraint(fields=["department", "item"], name="one_gonogo_per_item")]

    def __str__(self):
        return f"{self.department}: {self.item} agreed by {self.agreed_by}"


class Training(AuditedModel):
    """One person's training session, for the attendance sheet."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="training")
    session_date = models.DateField(default=timezone.localdate)
    topic = models.CharField(max_length=150, default="Practice case on the training copy")
    completed_without_help = models.BooleanField(default=False)
    trainer = models.CharField(max_length=150, blank=True)
    is_super_user = models.BooleanField(default=False, help_text="Answers this department's questions first.")

    class Meta:
        ordering = ["-session_date", "user__username"]

    def __str__(self):
        return f"{self.user} {self.session_date}"
