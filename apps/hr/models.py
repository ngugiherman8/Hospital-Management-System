import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.billing.models import MONEY
from apps.core.appendonly import AppendOnlyModel
from apps.core.models import AuditedModel, Sequence

LICENCE_WARNING_DAYS = 60


class Department(AuditedModel):
    name = models.CharField(max_length=100, unique=True)
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StaffProfile(AuditedModel):
    """One person who works here. Not everyone has a login, so the link to a user is optional."""

    audit_exclude = ("bank_account",)

    class Cadre(models.TextChoices):
        MEDICAL = "medical", "Doctor or dentist"
        NURSING = "nursing", "Nurse or midwife"
        ALLIED = "allied", "Laboratory, pharmacy or imaging"
        ADMIN = "admin", "Administration or finance"
        SUPPORT = "support", "Support (cleaning, security, kitchen, drivers)"

    staff_number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff_profile"
    )
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    national_id = models.CharField("National ID or passport", max_length=30, blank=True)
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    nssf_number = models.CharField("NSSF number", max_length=30, blank=True)
    shif_number = models.CharField("SHIF number", max_length=30, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    next_of_kin = models.CharField(max_length=120, blank=True)
    next_of_kin_phone = models.CharField(max_length=30, blank=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="staff")
    job_title = models.CharField(max_length=100)
    cadre = models.CharField(max_length=10, choices=Cadre.choices)
    date_joined = models.DateField()
    date_left = models.DateField(null=True, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    bank_account = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(date_left__isnull=True) | Q(date_left__gte=models.F("date_joined")),
                name="staff_left_after_joined",
            )
        ]

    def __str__(self):
        return f"{self.full_name} ({self.staff_number})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        if not self.staff_number:
            self.staff_number = f"S-{Sequence.next('staff_number'):05d}"
        super().save(*args, **kwargs)

    def is_active_on(self, day):
        return self.date_joined <= day and (self.date_left is None or self.date_left >= day)

    @property
    def is_active(self):
        return self.is_active_on(timezone.localdate())

    def contract_on(self, day=None):
        day = day or timezone.localdate()
        return (
            self.contracts.filter(start_date__lte=day)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=day))
            .order_by("-start_date")
            .first()
        )


class Contract(AuditedModel):
    class Kind(models.TextChoices):
        PERMANENT = "permanent", "Permanent"
        FIXED = "fixed", "Fixed term"
        CASUAL = "casual", "Casual"
        LOCUM = "locum", "Locum"
        INTERN = "intern", "Intern or attachment"

    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="contracts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Leave empty for a contract with no end date.")
    basic_salary = models.DecimalField(**MONEY, help_text="Monthly basic pay in KES.")
    hours_per_week = models.PositiveSmallIntegerField(default=45)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                name="contract_end_after_start",
            ),
            models.CheckConstraint(condition=Q(basic_salary__gte=0), name="contract_salary_not_negative"),
        ]

    def __str__(self):
        return f"{self.staff.full_name}: {self.get_kind_display()} from {self.start_date}"


class Licence(AuditedModel):
    class Body(models.TextChoices):
        KMPDC = "kmpdc", "Kenya Medical Practitioners and Dentists Council"
        NCK = "nck", "Nursing Council of Kenya"
        PPB = "ppb", "Pharmacy and Poisons Board"
        COC = "coc", "Clinical Officers Council"
        KMLTTB = "kmlttb", "Kenya Medical Laboratory Technicians and Technologists Board"
        NUTRITION = "nutrition", "Nutritionists and Dieticians Institute"
        OTHER = "other", "Other body"

    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="licences")
    body = models.CharField(max_length=10, choices=Body.choices)
    number = models.CharField(max_length=60)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField()
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["expires_on"]

    def __str__(self):
        return f"{self.get_body_display()} {self.number}"

    def status(self, today=None):
        today = today or timezone.localdate()
        if self.expires_on < today:
            return "expired"
        if self.expires_on <= today + datetime.timedelta(days=LICENCE_WARNING_DAYS):
            return "due"
        return "valid"


class PublicHoliday(AuditedModel):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.name} {self.date}"


class LeaveType(AuditedModel):
    name = models.CharField(max_length=60, unique=True)
    annual_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=0, help_text="Days earned per year (added month by month)."
    )
    uses_balance = models.BooleanField(
        default=True, help_text="Tick if the days come out of a balance. Untick for leave that is not counted."
    )
    is_paid = models.BooleanField(default=True, help_text="Unpaid leave is taken off the pay at the end of the month.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LeaveRequest(AuditedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for a decision"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=6, decimal_places=2, editable=False)
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="leave_dates_ok")]

    def __str__(self):
        return f"{self.staff.full_name}: {self.leave_type} {self.start_date} to {self.end_date}"


class LeaveEntry(AppendOnlyModel):
    """The leave ledger. Balance is the sum. Mistakes are fixed with a reversing entry, never an edit."""

    class Kind(models.TextChoices):
        ACCRUAL = "accrual", "Earned"
        TAKEN = "taken", "Taken"
        ADJUST = "adjust", "Adjustment"
        REVERSAL = "reversal", "Put back"

    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="leave_entries")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    days = models.DecimalField(max_digits=6, decimal_places=2, help_text="Positive adds, negative takes away.")
    request = models.ForeignKey(LeaveRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    source_key = models.CharField(max_length=80, unique=True, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=~Q(days=0), name="leave_entry_not_zero")]
