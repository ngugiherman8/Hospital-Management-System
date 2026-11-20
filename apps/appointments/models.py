import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.accounts.models import Role
from apps.core.models import AuditedModel, Sequence
from apps.patients.models import Patient


class Clinic(models.TextChoices):
    GENERAL = "general", "General outpatient"
    MCH = "mch", "Maternal and child health"
    DENTAL = "dental", "Dental"
    EYE = "eye", "Eye"


class Status(models.TextChoices):
    BOOKED = "booked", "Booked"
    WAITING_TRIAGE = "waiting_triage", "Waiting for triage"
    WAITING_DOCTOR = "waiting_doctor", "Waiting for doctor"
    WITH_DOCTOR = "with_doctor", "With doctor"
    AWAITING_RESULTS = "awaiting_results", "Awaiting results"
    ADMITTED = "admitted", "Admitted"
    COMPLETED = "completed", "Completed"
    NO_SHOW = "no_show", "Did not come"
    CANCELLED = "cancelled", "Cancelled"


# status -> {next status: roles allowed to make the change}
TRANSITIONS = {
    Status.BOOKED: {
        Status.WAITING_TRIAGE: (Role.RECEPTION,),
        Status.NO_SHOW: (Role.RECEPTION,),
        Status.CANCELLED: (Role.RECEPTION,),
    },
    Status.WAITING_TRIAGE: {
        Status.WAITING_DOCTOR: (Role.NURSE,),
        Status.CANCELLED: (Role.RECEPTION,),
    },
    Status.WAITING_DOCTOR: {
        Status.WITH_DOCTOR: (Role.DOCTOR,),
        Status.CANCELLED: (Role.RECEPTION,),
    },
    Status.WITH_DOCTOR: {
        Status.AWAITING_RESULTS: (Role.DOCTOR,),
        Status.ADMITTED: (Role.DOCTOR,),
        Status.COMPLETED: (Role.DOCTOR,),
    },
    Status.AWAITING_RESULTS: {
        # The lab sends the patient back once every test has a result; the doctor can too
        # (for example after cancelling the tests that were still open).
        Status.WITH_DOCTOR: (Role.LAB, Role.DOCTOR, Role.RADIOLOGIST),
    },
    Status.ADMITTED: {
        Status.COMPLETED: (Role.DOCTOR,),
    },
}

# Moves that depend on the clinic: (clinic, from, to) -> extra roles. Dental patients skip triage and go
# straight to the dentist; antenatal and postnatal visits are run by the midwife.
CLINIC_MOVES = {
    ("dental", Status.BOOKED, Status.WAITING_DOCTOR): (Role.RECEPTION,),
    ("dental", Status.WAITING_TRIAGE, Status.WAITING_DOCTOR): (Role.RECEPTION,),
    ("dental", Status.WAITING_DOCTOR, Status.WITH_DOCTOR): (Role.DENTIST,),
    ("dental", Status.WITH_DOCTOR, Status.AWAITING_RESULTS): (Role.DENTIST,),
    ("dental", Status.WITH_DOCTOR, Status.COMPLETED): (Role.DENTIST,),
    ("dental", Status.AWAITING_RESULTS, Status.WITH_DOCTOR): (Role.DENTIST,),
    ("mch", Status.WAITING_DOCTOR, Status.WITH_DOCTOR): (Role.MIDWIFE,),
    ("mch", Status.WITH_DOCTOR, Status.AWAITING_RESULTS): (Role.MIDWIFE,),
    ("mch", Status.WITH_DOCTOR, Status.COMPLETED): (Role.MIDWIFE,),
    ("mch", Status.AWAITING_RESULTS, Status.WITH_DOCTOR): (Role.MIDWIFE,),
}

ACTIVE_STATUSES = [
    Status.BOOKED,
    Status.WAITING_TRIAGE,
    Status.WAITING_DOCTOR,
    Status.WITH_DOCTOR,
    Status.AWAITING_RESULTS,
    Status.ADMITTED,
]


class TransitionBlocked(Exception):
    """The role is allowed to make this move, but something must be recorded first."""


def _need_triage(visit):
    if visit.clinic == Clinic.DENTAL:
        return None  # triage is optional for dental patients
    if getattr(visit, "triage", None) is None:
        return "Save the triage record first."


def _need_an_order(visit):
    live = ("ordered", "sample_taken", "in_progress", "result_in")
    if not visit.orders.filter(items__status__in=live).exists():
        return "Order at least one test first."


def _need_all_results(visit):
    if visit.orders.filter(items__status__in=("ordered", "sample_taken", "in_progress")).exists():
        return "Every ordered test and scan needs a result (a preliminary report for scans) or must be cancelled first."


def _need_diagnosis_and_outcome(visit):
    consultation = getattr(visit, "consultation", None)
    if consultation is None or not consultation.diagnoses.exists():
        return "Save at least one diagnosis first."
    if consultation.outcome not in ("home", "refer"):
        return 'Set the outcome to "go home" or "refer" first (or admit the patient).'


def _need_admission(visit):
    if getattr(visit, "admission", None) is None:
        return "Admit the patient to a bed first."


def _need_discharge(visit):
    admission = getattr(visit, "admission", None)
    if admission is None or admission.discharged_at is None:
        return "Discharge the patient with a summary first."


# (from, to) -> check that returns a message when the move is not yet allowed
CONDITIONS = {
    (Status.WAITING_TRIAGE, Status.WAITING_DOCTOR): _need_triage,
    (Status.BOOKED, Status.WAITING_DOCTOR): _need_triage,
    (Status.WITH_DOCTOR, Status.AWAITING_RESULTS): _need_an_order,
    (Status.AWAITING_RESULTS, Status.WITH_DOCTOR): _need_all_results,
    (Status.WITH_DOCTOR, Status.COMPLETED): _need_diagnosis_and_outcome,
    (Status.WITH_DOCTOR, Status.ADMITTED): _need_admission,
    (Status.ADMITTED, Status.COMPLETED): _need_discharge,
}


class Appointment(AuditedModel):
    class Kind(models.TextChoices):
        WALK_IN = "walk_in", "Walk-in"
        BOOKED = "booked", "Booked"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="appointments")
    clinic = models.CharField(max_length=20, choices=Clinic.choices, default=Clinic.GENERAL)
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appointments",
        limit_choices_to={"role": Role.DOCTOR},
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.WALK_IN)
    visit_date = models.DateField(default=timezone.localdate, db_index=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, help_text="Leave empty for walk-ins.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED, db_index=True)
    queue_number = models.PositiveIntegerField(null=True, blank=True, editable=False)
    reason = models.CharField(max_length=300, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True, editable=False)
    emergency_reason = models.CharField(
        max_length=300, blank=True, editable=False, help_text="Set when sent to a doctor before triage."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        ordering = ["visit_date", "queue_number", "scheduled_for"]
        indexes = [models.Index(fields=["visit_date", "status"])]

    def __str__(self):
        return f"{self.patient.full_name}, {self.visit_date:%d %b %Y}"

    @property
    def queue_label(self):
        return f"A-{self.queue_number:03d}" if self.queue_number else ""

    @property
    def minutes_waiting(self):
        if self.arrived_at and self.status in (Status.WAITING_TRIAGE, Status.WAITING_DOCTOR):
            return int((timezone.now() - self.arrived_at).total_seconds() // 60)
        return None

    def clean(self):
        if self.scheduled_for:
            self.visit_date = timezone.localtime(self.scheduled_for).date()
            if self.pk is None and self.scheduled_for < timezone.now() - datetime.timedelta(minutes=5):
                raise ValidationError({"scheduled_for": "Choose a time that has not passed."})
            if self.doctor_id:
                clash = Appointment.objects.filter(
                    doctor_id=self.doctor_id, scheduled_for=self.scheduled_for, status__in=ACTIVE_STATUSES
                ).exclude(pk=self.pk)
                if clash.exists():
                    raise ValidationError({"scheduled_for": "That doctor already has an appointment at this time."})
        elif self.kind == self.Kind.BOOKED:
            raise ValidationError({"scheduled_for": "Booked appointments need a date and time."})

    def _assign_queue_number(self):
        if not self.queue_number:
            self.queue_number = Sequence.next(f"queue-{self.visit_date.isoformat()}")

    def save(self, *args, **kwargs):
        if self.kind == self.Kind.WALK_IN and not self.pk:
            # Walk-ins are in the queue straight away.
            self.status = Status.WAITING_TRIAGE
            self.arrived_at = timezone.now()
            self._assign_queue_number()
        super().save(*args, **kwargs)

    def _options(self):
        options = {to: tuple(roles) for to, roles in TRANSITIONS.get(self.status, {}).items()}
        for (clinic, frm, to), roles in CLINIC_MOVES.items():
            if clinic == self.clinic and frm == self.status:
                options[to] = tuple(dict.fromkeys(options.get(to, ()) + roles))
        return options

    def allowed_next(self, user):
        """Statuses this user may move the appointment to."""
        return [s for s, roles in self._options().items() if user.has_role(*roles)]

    def move_to(self, new_status, user):
        if new_status not in self.allowed_next(user):
            raise PermissionError(f"Cannot change from {self.get_status_display()} to {Status(new_status).label}.")
        before = self.status
        check = CONDITIONS.get((self.status, new_status))
        message = check(self) if check else None
        if message:
            raise TransitionBlocked(message)
        self.status = new_status
        if new_status == Status.WAITING_TRIAGE or (new_status == Status.WAITING_DOCTOR and before == Status.BOOKED):
            self.arrived_at = timezone.now()
            self.visit_date = timezone.localdate()
            self._assign_queue_number()
        self.save()

    def fast_track(self, user, reason):
        """Send an emergency straight to a doctor before triage. The reason goes in the audit log."""
        if self.status != Status.WAITING_TRIAGE or not user.has_role(Role.RECEPTION, Role.NURSE, Role.DOCTOR):
            raise PermissionError("Only a patient waiting for triage can be fast-tracked.")
        reason = (reason or "").strip()
        if not reason:
            raise TransitionBlocked("Give a reason for sending the patient to the doctor before triage.")
        self.status = Status.WAITING_DOCTOR
        self.emergency_reason = reason[:300]
        self.save()

    @property
    def is_emergency(self):
        triage = getattr(self, "triage", None)
        return bool(self.emergency_reason) or (triage is not None and triage.priority == "emergency")
