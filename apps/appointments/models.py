from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.core.audit import AuditedModel


class Clinic(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slot_minutes = models.PositiveSmallIntegerField(default=15)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AppointmentStatus(models.TextChoices):
    BOOKED = "booked", "Booked"
    ARRIVED = "arrived", "Arrived"
    SEEN = "seen", "Seen"
    NO_SHOW = "no_show", "No-show"
    CANCELLED = "cancelled", "Cancelled"


class Appointment(AuditedModel):
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="appointments")
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="appointments")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="appointments",
        limit_choices_to={"role": "doctor"},
    )
    starts_at = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=12, choices=AppointmentStatus.choices, default=AppointmentStatus.BOOKED, db_index=True
    )
    reason = models.CharField(max_length=200, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    audit_exclude = ("reminder_sent_at",)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "starts_at"],
                condition=~models.Q(status__in=["cancelled", "no_show"]),
                name="no_double_booking",
            )
        ]

    def __str__(self):
        return f"{self.patient.full_name} with {self.doctor} at {self.starts_at:%d %b %H:%M}"


class Stage(models.TextChoices):
    WAITING_TRIAGE = "waiting_triage", "Waiting for triage"
    WAITING_DOCTOR = "waiting_doctor", "Waiting for doctor"
    WITH_DOCTOR = "with_doctor", "With doctor"
    AT_LAB = "at_lab", "At lab"
    ADMITTED = "admitted", "Admitted"
    DONE = "done", "Completed"


class TransitionError(Exception):
    """A visit step that is not allowed right now. The message is safe to show to staff."""


def _visit(entry):
    from apps.clinical.models import Visit

    return Visit.objects.filter(queue_entry=entry).first()


def _check_triage_saved(entry, **ctx):
    v = _visit(entry)
    return None if v and v.triaged_at else "Save the triage record first."


def _check_fast_track(entry, **ctx):
    return None if (ctx.get("reason") or "").strip() else "Give the reason this patient is going straight to a doctor."


def _check_has_orders(entry, **ctx):
    v = _visit(entry)
    ok = v and v.orders.exclude(status="cancelled").exists()
    return None if ok else "Order at least one test first."


def _check_results_in(entry, **ctx):
    v = _visit(entry)
    if v and v.orders.filter(status__in=["ordered", "in_progress"]).exists():
        return "Every test needs a result or must be cancelled first."
    return None


def _check_can_complete(entry, **ctx):
    v = _visit(entry)
    if not v or not v.diagnoses.exists():
        return "Add at least one diagnosis first."
    if v.outcome not in ("home", "refer"):
        return "Choose 'go home' or 'refer' as the outcome."
    if v.outcome == "refer" and not v.referred_to.strip():
        return "Say which facility the patient is referred to."
    return None


def _check_admission_exists(entry, **ctx):
    from apps.wards.models import Admission

    ok = Admission.objects.filter(visit__queue_entry=entry, status="admitted").exists()
    return None if ok else "Create the admission (choose a free bed) first."


def _check_discharged(entry, **ctx):
    from apps.wards.models import Admission

    ok = Admission.objects.filter(visit__queue_entry=entry, status="discharged").exclude(summary="").exists()
    return None if ok else "Save the discharge summary first."


class Transition:
    def __init__(self, sources, target, roles, check=None):
        self.sources, self.target, self.roles, self.check = frozenset(sources), target, tuple(roles), check


# The single place that says who may move a visit to which step, and what must be true first.
TRANSITIONS = {
    "save_triage": Transition({Stage.WAITING_TRIAGE}, Stage.WAITING_DOCTOR, ("nurse",), _check_triage_saved),
    "fast_track": Transition({Stage.WAITING_TRIAGE}, Stage.WAITING_DOCTOR, ("receptionist",), _check_fast_track),
    "call_in": Transition({Stage.WAITING_DOCTOR}, Stage.WITH_DOCTOR, ("doctor",)),
    "send_to_lab": Transition({Stage.WITH_DOCTOR}, Stage.AT_LAB, ("doctor",), _check_has_orders),
    "lab_complete": Transition({Stage.AT_LAB}, Stage.WITH_DOCTOR, ("lab", "doctor", "nurse"), _check_results_in),
    "complete": Transition({Stage.WITH_DOCTOR}, Stage.DONE, ("doctor",), _check_can_complete),
    "admit": Transition({Stage.WITH_DOCTOR}, Stage.ADMITTED, ("doctor",), _check_admission_exists),
    "discharge": Transition({Stage.ADMITTED}, Stage.DONE, ("doctor",), _check_discharged),
}


# Which stages belong to each queue filter tab
STAGE_GROUPS = {
    "triage": [Stage.WAITING_TRIAGE],
    "doctor": [Stage.WAITING_DOCTOR, Stage.WITH_DOCTOR],
    "lab": [Stage.AT_LAB],
}


class QueueEntry(AuditedModel):
    """One patient's place in today's queue (walk-in or arrived appointment)."""

    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="queue_entries")
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="queue_entries")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="queue_entries"
    )
    appointment = models.OneToOneField(
        Appointment, null=True, blank=True, on_delete=models.SET_NULL, related_name="queue_entry"
    )
    queue_date = models.DateField(default=timezone.localdate, db_index=True)
    number = models.PositiveIntegerField()
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.WAITING_TRIAGE, db_index=True)
    room = models.CharField(max_length=20, blank=True)
    arrived_at = models.DateTimeField(default=timezone.now)
    stage_changed_at = models.DateTimeField(default=timezone.now)
    is_walk_in = models.BooleanField(default=True)

    class Meta:
        ordering = ["queue_date", "number"]
        verbose_name_plural = "queue entries"
        constraints = [
            models.UniqueConstraint(fields=["queue_date", "number"], name="unique_ticket_per_day"),
            models.UniqueConstraint(
                fields=["patient", "queue_date"],
                condition=~models.Q(stage="done"),
                name="one_open_queue_entry_per_patient_per_day",
            ),
        ]

    @property
    def ticket(self):
        return f"A-{self.number:03d}"

    @property
    def minutes_waiting(self):
        return int((timezone.now() - self.stage_changed_at).total_seconds() // 60)

    @classmethod
    def join(cls, *, patient, clinic, doctor=None, appointment=None):
        """Add a patient to today's queue. Returns the existing open entry if there is one."""
        today = timezone.localdate()
        existing = cls.objects.filter(patient=patient, queue_date=today).exclude(stage=Stage.DONE).first()
        if existing:
            return existing, False
        for _ in range(5):
            try:
                with transaction.atomic():
                    last = cls.objects.filter(queue_date=today).aggregate(m=models.Max("number"))["m"] or 0
                    entry = cls.objects.create(
                        patient=patient,
                        clinic=clinic,
                        doctor=doctor,
                        appointment=appointment,
                        queue_date=today,
                        number=last + 1,
                        is_walk_in=appointment is None,
                    )
                return entry, True
            except IntegrityError:
                continue  # another receptionist took the number; try the next one
        raise RuntimeError("Could not allocate a queue number")

    def apply(self, action, user, **ctx):
        """Take one step in TRANSITIONS. Raises PermissionDenied (wrong role) or TransitionError (not allowed now)."""
        from django.core.exceptions import PermissionDenied

        t = TRANSITIONS[action]
        if not user.has_role(*t.roles):
            raise PermissionDenied
        if self.stage not in t.sources:
            raise TransitionError(f"{self.ticket} is at '{self.get_stage_display()}', so that step is not available.")
        if t.check:
            problem = t.check(self, **ctx)
            if problem:
                raise TransitionError(problem)
        self.move_to(t.target, room=ctx.get("room", ""))
        return self

    def move_to(self, stage, room=""):
        self.stage = stage
        self.stage_changed_at = timezone.now()
        if room:
            self.room = room
        self.save()

    def __str__(self):
        return f"{self.ticket} {self.patient.full_name}"
