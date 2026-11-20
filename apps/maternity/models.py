import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditedModel
from apps.patients.models import Patient

PREGNANCY_DAYS = 280  # 40 weeks from the last menstrual period

DANGER_SIGNS = [
    ("bleeding", "Vaginal bleeding"),
    ("headache", "Severe headache"),
    ("vision", "Blurred vision"),
    ("convulsions", "Convulsions"),
    ("abdominal_pain", "Severe abdominal pain"),
    ("fever", "Fever"),
    ("reduced_movement", "Reduced or no fetal movement"),
    ("fluid", "Leaking fluid"),
    ("swelling", "Swelling of face or hands"),
    ("breathing", "Difficulty breathing"),
]
POSTNATAL_DANGER_SIGNS = [
    ("heavy_bleeding", "Heavy bleeding"),
    ("fever", "Fever"),
    ("wound", "Wound or perineum infection"),
    ("headache", "Severe headache or blurred vision"),
    ("convulsions", "Convulsions"),
    ("mood", "Very low mood or distress"),
    ("poor_feeding", "Baby feeding poorly"),
    ("jaundice", "Baby yellow"),
    ("cord", "Cord red or discharging"),
    ("baby_breathing", "Baby breathing fast or with difficulty"),
]


class ContactSchedule(AuditedModel):
    """When each contact is due. Data, so the hospital can change it without new code."""

    class Care(models.TextChoices):
        ANTENATAL = "antenatal", "Antenatal"
        POSTNATAL = "postnatal", "Postnatal"

    care = models.CharField(max_length=10, choices=Care.choices)
    contact_number = models.PositiveSmallIntegerField()
    due_at = models.PositiveSmallIntegerField(
        help_text="Weeks of pregnancy (antenatal) or days after birth (postnatal)."
    )
    grace_days = models.PositiveSmallIntegerField(
        default=7, help_text="Days after the due date before it counts as missed."
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["care", "contact_number"]
        constraints = [models.UniqueConstraint(fields=["care", "contact_number"], name="unique_schedule_contact")]

    def __str__(self):
        unit = "weeks" if self.care == self.Care.ANTENATAL else "days"
        return f"{self.get_care_display()} contact {self.contact_number}: {self.due_at} {unit}"


def schedule_for(care):
    return list(ContactSchedule.objects.filter(care=care))


class PregnancyEpisode(AuditedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DELIVERED = "delivered", "Delivered"
        ENDED = "ended", "Ended (miscarriage or other)"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="pregnancies")
    lmp = models.DateField("Last menstrual period")
    edd = models.DateField("Due date", blank=True, help_text="Worked out from the LMP; change after an ultrasound.")
    previous_pregnancies = models.PositiveSmallIntegerField(default=0)
    previous_births = models.PositiveSmallIntegerField(default=0)
    blood_group = models.CharField(max_length=5, blank=True)
    risk_factors = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    ended_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-lmp"]
        constraints = [
            models.UniqueConstraint(
                fields=["patient"], condition=Q(status="active"), name="one_active_pregnancy_per_patient"
            )
        ]

    def __str__(self):
        return f"Pregnancy of {self.patient.full_name} (due {self.edd:%d %b %Y})"

    def clean(self):
        if self.lmp and self.lmp > timezone.localdate():
            raise ValidationError({"lmp": "The last menstrual period cannot be in the future."})

    def save(self, *args, **kwargs):
        if self.lmp and not self.edd:
            self.edd = self.lmp + datetime.timedelta(days=PREGNANCY_DAYS)
        super().save(*args, **kwargs)

    @property
    def start_date(self):
        """Day zero of the pregnancy. Follows the due date, so an ultrasound correction moves the schedule."""
        return self.edd - datetime.timedelta(days=PREGNANCY_DAYS)

    def gestation(self, on=None):
        """(weeks, days) of pregnancy on a date, default today."""
        on = on or timezone.localdate()
        days = max((on - self.start_date).days, 0)
        return days // 7, days % 7

    @property
    def gestation_label(self):
        weeks, days = self.gestation()
        return f"{weeks} weeks {days} days"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @property
    def has_danger_flag(self):
        return bool(self.risk_factors.strip()) or any(c.needs_review for c in self.contacts.all())

    def next_contact(self, today=None):
        """The next scheduled contact not yet done, with its due date and status (upcoming, due, missed)."""
        today = today or timezone.localdate()
        done = self.contacts.count()
        for row in schedule_for(ContactSchedule.Care.ANTENATAL):
            if row.contact_number > done:
                due = self.start_date + datetime.timedelta(weeks=row.due_at)
                return row, due, _status(today, due, row.grace_days)
        return None

    def next_visit(self):
        last = self.contacts.first()
        if last and last.next_visit:
            return last.next_visit
        nxt = self.next_contact()
        return nxt[1] if nxt else None


def _status(today, due, grace):
    if today > due + datetime.timedelta(days=grace):
        return "missed"
    if today >= due - datetime.timedelta(days=7):
        return "due"
    return "upcoming"


class AntenatalContact(AuditedModel):
    """One antenatal visit. Never edited: a correction is entered as a new contact that points to it."""

    class Presentation(models.TextChoices):
        UNKNOWN = "", "Not assessed"
        CEPHALIC = "cephalic", "Head down"
        BREECH = "breech", "Breech"
        TRANSVERSE = "transverse", "Transverse"

    class Hiv(models.TextChoices):
        NOT_TESTED = "", "Not recorded"
        NEGATIVE = "negative", "Negative"
        POSITIVE = "positive", "Positive"

    episode = models.ForeignKey(PregnancyEpisode, on_delete=models.PROTECT, related_name="contacts")
    number = models.PositiveSmallIntegerField(editable=False)
    date = models.DateField(default=timezone.localdate)
    weeks = models.PositiveSmallIntegerField(editable=False)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    fundal_height_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    fetal_heartbeat = models.PositiveSmallIntegerField("Fetal heartbeat (per min)", null=True, blank=True)
    presentation = models.CharField(max_length=12, choices=Presentation.choices, blank=True)
    urine_protein = models.CharField(max_length=20, blank=True)
    haemoglobin = models.DecimalField("Haemoglobin (g/dL)", max_digits=4, decimal_places=1, null=True, blank=True)
    hiv_status = models.CharField("HIV status", max_length=10, choices=Hiv.choices, blank=True)
    danger_signs = models.JSONField(default=list, blank=True)
    medicines_given = models.CharField(max_length=300, blank=True)
    birth_plan = models.TextField(blank=True)
    next_visit = models.DateField(null=True, blank=True)
    referral = models.CharField(max_length=300, blank=True)
    corrects = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections", editable=False
    )
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["episode", "number"], name="unique_antenatal_contact_number")]

    def __str__(self):
        return f"Antenatal contact {self.number} on {self.date:%d %b %Y}"

    @property
    def needs_review(self):
        high_bp = (self.bp_systolic or 0) >= 140 or (self.bp_diastolic or 0) >= 90
        return bool(self.danger_signs) or high_bp or self.presentation in ("breech", "transverse")

    @property
    def danger_labels(self):
        names = dict(DANGER_SIGNS)
        return [names.get(c, c) for c in self.danger_signs]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Saved contacts cannot be changed; enter a correction.")
        with transaction.atomic():
            if not self.number:
                last = (
                    AntenatalContact.objects.select_for_update()
                    .filter(episode=self.episode)
                    .order_by("-number")
                    .first()
                )
                self.number = (last.number if last else 0) + 1
            self.weeks = self.episode.gestation(self.date)[0]
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Saved contacts cannot be deleted.")


class DeliverySummary(AuditedModel):
    class Mode(models.TextChoices):
        VAGINAL = "vaginal", "Normal vaginal"
        ASSISTED = "assisted", "Assisted (vacuum or forceps)"
        CAESAREAN = "caesarean", "Caesarean section"

    class Place(models.TextChoices):
        FACILITY = "facility", "This facility"
        OTHER_FACILITY = "other_facility", "Another facility"
        HOME = "home", "Home"

    class BabyOutcome(models.TextChoices):
        ALIVE = "alive", "Born alive"
        STILLBIRTH = "stillbirth", "Stillbirth"

    episode = models.OneToOneField(PregnancyEpisode, on_delete=models.PROTECT, related_name="delivery")
    delivered_at = models.DateTimeField()
    place = models.CharField(max_length=20, choices=Place.choices, default=Place.FACILITY)
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.VAGINAL)
    attended_by = models.CharField(max_length=150, blank=True)
    mother_outcome = models.CharField(max_length=200, default="Well")
    baby_outcome = models.CharField(max_length=12, choices=BabyOutcome.choices, default=BabyOutcome.ALIVE)
    complications = models.TextField(blank=True)
    baby = models.OneToOneField(
        Patient, null=True, blank=True, on_delete=models.PROTECT, related_name="birth", editable=False
    )
    birth_weight_kg = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    feeding_notes = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return f"Delivery of {self.episode.patient.full_name}, {self.delivered_at:%d %b %Y}"

    @property
    def birth_date(self):
        return timezone.localtime(self.delivered_at).date()

    def pending_contacts(self, today=None):
        """Postnatal contacts not yet done for the mother and the baby, with due date and status."""
        today = today or timezone.localdate()
        rows = schedule_for(ContactSchedule.Care.POSTNATAL)
        out = []
        people = [("mother", self.episode.patient)]
        if self.baby_id:
            people.append(("baby", self.baby))
        for who, person in people:
            done = self.postnatal_contacts.filter(who=who).count()
            for row in rows:
                if row.contact_number > done:
                    due = self.birth_date + datetime.timedelta(days=row.due_at)
                    out.append((who, person, row, due, _status(today, due, row.grace_days)))
                    break  # only the next contact for each person
        return out


class PostnatalContact(AuditedModel):
    class Who(models.TextChoices):
        MOTHER = "mother", "Mother"
        BABY = "baby", "Baby"

    delivery = models.ForeignKey(DeliverySummary, on_delete=models.PROTECT, related_name="postnatal_contacts")
    who = models.CharField(max_length=6, choices=Who.choices)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="postnatal_contacts")
    number = models.PositiveSmallIntegerField(editable=False)
    date = models.DateField(default=timezone.localdate)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    findings = models.TextField(
        blank=True,
        help_text="Mother: bleeding, wound, breasts, mood, family-planning advice. Baby: feeding, cord, jaundice.",
    )
    danger_signs = models.JSONField(default=list, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["who", "-number"]
        constraints = [models.UniqueConstraint(fields=["delivery", "who", "number"], name="unique_postnatal_number")]

    def __str__(self):
        return f"Postnatal contact {self.number} ({self.who})"

    @property
    def danger_labels(self):
        names = dict(POSTNATAL_DANGER_SIGNS)
        return [names.get(c, c) for c in self.danger_signs]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Saved contacts cannot be changed; enter a correction.")
        with transaction.atomic():
            if not self.number:
                last = (
                    PostnatalContact.objects.select_for_update()
                    .filter(delivery=self.delivery, who=self.who)
                    .order_by("-number")
                    .first()
                )
                self.number = (last.number if last else 0) + 1
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Saved contacts cannot be deleted.")


def record_delivery(episode, user, baby_sex="", baby_name="", **fields):
    """Save the delivery summary, close the episode and create the newborn's own record linked to the mother."""
    with transaction.atomic():
        episode = PregnancyEpisode.objects.select_for_update().get(pk=episode.pk)
        if not episode.is_active:
            raise ValidationError("This pregnancy is already closed.")
        mother = episode.patient
        delivery = DeliverySummary(episode=episode, recorded_by=user, **fields)
        if delivery.baby_outcome == DeliverySummary.BabyOutcome.ALIVE:
            if baby_sex not in ("F", "M"):
                raise ValidationError("Record the baby's sex.")
            baby = Patient.objects.create(
                first_name=baby_name.strip() or f"Baby of {mother.first_name}",
                last_name=mother.last_name,
                date_of_birth=timezone.localtime(delivery.delivered_at).date(),
                sex=baby_sex,
                id_type=Patient.IdType.NONE,
                residence=mother.residence,
                kin_name=mother.full_name,
                kin_relationship="Mother",
                kin_phone=mother.phone,
                payer=mother.payer,
                payer_name=mother.payer_name,
                member_number=mother.member_number,
                mother=mother,
            )
            delivery.baby = baby
        delivery.save()
        episode.status = PregnancyEpisode.Status.DELIVERED
        episode.save()
        return delivery
