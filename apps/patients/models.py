import re

from django.db import models, transaction
from django.urls import reverse

from apps.core.audit import AuditedModel


def normalize_phone(raw: str) -> str:
    """Kenyan numbers to a canonical 2547XXXXXXXX / 2541XXXXXXXX form where possible."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if len(digits) == 9 and digits[0] in "17":
        return "254" + digits
    return digits


def normalize_name(*parts: str) -> str:
    return " ".join(re.sub(r"[^a-z\s]", "", p.lower()).strip() for p in parts if p).strip()


class HospitalNumberCounter(models.Model):
    """Single-row counter; locked with select_for_update so numbers are never reused."""

    last_value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next_number(cls) -> str:
        with transaction.atomic():
            counter, _ = cls.objects.select_for_update().get_or_create(pk=1)
            counter.last_value += 1
            counter.save(update_fields=["last_value"])
            return f"HN-{counter.last_value:06d}"


class Sex(models.TextChoices):
    FEMALE = "F", "Female"
    MALE = "M", "Male"
    OTHER = "O", "Other / not stated"


class IdType(models.TextChoices):
    NATIONAL_ID = "national_id", "National ID"
    PASSPORT = "passport", "Passport"
    BIRTH_CERT = "birth_cert", "Birth certificate no."
    ALIEN_ID = "alien_id", "Alien ID"
    NONE = "none", "No ID"


class Patient(AuditedModel):
    hospital_number = models.CharField(max_length=20, unique=True, editable=False)
    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=1, choices=Sex.choices)
    id_type = models.CharField(max_length=20, choices=IdType.choices, default=IdType.NATIONAL_ID)
    id_number = models.CharField(max_length=40, blank=True)

    phone = models.CharField(max_length=20)
    phone_normalized = models.CharField(max_length=15, db_index=True, editable=False)
    area_of_residence = models.CharField(max_length=120, blank=True)

    next_of_kin_name = models.CharField(max_length=120, blank=True)
    next_of_kin_relationship = models.CharField(max_length=40, blank=True)
    next_of_kin_phone = models.CharField(max_length=20, blank=True)
    allergies = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["id_type", "id_number"],
                condition=~models.Q(id_number="") & ~models.Q(id_type="none"),
                name="unique_patient_id_document",
            )
        ]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def save(self, *args, **kwargs):
        if not self.hospital_number:
            self.hospital_number = HospitalNumberCounter.next_number()
        self.phone_normalized = normalize_phone(self.phone)
        self.first_name = self.first_name.strip()
        self.last_name = self.last_name.strip()
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self):
        from django.utils import timezone

        today = timezone.localdate()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def masked_id(self):
        if not self.id_number:
            return ""
        return "•••" + self.id_number[-4:]

    def get_absolute_url(self):
        return reverse("patients:detail", args=[self.pk])

    def __str__(self):
        return f"{self.full_name} ({self.hospital_number})"


class PayerType(models.TextChoices):
    CASH = "cash", "Cash / M-Pesa"
    SHA = "sha", "SHA"
    PRIVATE = "private", "Private insurer"
    CORPORATE = "corporate", "Corporate"


class InsuranceCover(AuditedModel):
    """How a patient pays. Cash is the default; one cover is marked primary."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="covers")
    payer_type = models.CharField(max_length=12, choices=PayerType.choices, default=PayerType.CASH)
    insurer_name = models.CharField(max_length=120, blank=True)
    member_number = models.CharField(max_length=60, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    is_primary = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_primary", "-id"]

    def __str__(self):
        label = self.get_payer_type_display()
        return f"{label} {self.member_number}".strip()
