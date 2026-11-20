import re

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditedModel, Sequence


def normalize_phone(raw):
    """Return a Kenyan number as +2547XXXXXXXX where possible, else the digits."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("254") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+254" + digits[1:]
    if len(digits) == 9 and digits[0] in "71":
        return "+254" + digits
    return digits


class Patient(AuditedModel):
    class Sex(models.TextChoices):
        FEMALE = "F", "Female"
        MALE = "M", "Male"

    class IdType(models.TextChoices):
        NATIONAL_ID = "national_id", "National ID"
        PASSPORT = "passport", "Passport"
        BIRTH_CERT = "birth_cert", "Birth certificate"
        NONE = "none", "None"

    class Payer(models.TextChoices):
        CASH = "cash", "Cash / M-Pesa"
        SHA = "sha", "SHA"
        INSURER = "insurer", "Private insurer"
        CORPORATE = "corporate", "Corporate"

    hospital_number = models.CharField(max_length=20, unique=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=1, choices=Sex.choices)
    id_type = models.CharField(max_length=20, choices=IdType.choices, default=IdType.NATIONAL_ID)
    id_number = models.CharField(max_length=50, blank=True, db_index=True)

    phone = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    residence = models.CharField(max_length=200, blank=True)

    kin_name = models.CharField(max_length=150, blank=True)
    kin_relationship = models.CharField(max_length=50, blank=True)
    kin_phone = models.CharField(max_length=20, blank=True)

    allergies = models.TextField(blank=True)

    payer = models.CharField(max_length=20, choices=Payer.choices, default=Payer.CASH)
    payer_name = models.CharField("Insurer or company", max_length=150, blank=True)
    member_number = models.CharField(max_length=60, blank=True)

    mother = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children", editable=False
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["last_name", "date_of_birth"])]

    def __str__(self):
        return f"{self.full_name} ({self.hospital_number})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        today = timezone.localdate()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    def save(self, *args, **kwargs):
        self.first_name = self.first_name.strip()
        self.last_name = self.last_name.strip()
        self.phone = normalize_phone(self.phone)
        self.kin_phone = normalize_phone(self.kin_phone)
        if not self.hospital_number:
            number = Sequence.next("hospital_number")
            self.hospital_number = f"{settings.HOSPITAL_NUMBER_PREFIX}-{number:06d}"
        super().save(*args, **kwargs)

    @classmethod
    def find_possible_duplicates(
        cls, first_name, last_name, date_of_birth=None, phone="", id_number="", exclude_pk=None
    ):
        """People who may already be registered: same ID or phone, or similar name and birth date."""
        query = Q()
        if id_number:
            query |= Q(id_number__iexact=id_number.strip())
        phone = normalize_phone(phone)
        if phone:
            query |= Q(phone=phone)
        if date_of_birth and last_name:
            query |= Q(last_name__iexact=last_name.strip(), date_of_birth=date_of_birth)
            query |= Q(
                first_name__iexact=first_name.strip(),
                date_of_birth=date_of_birth,
            )
        if not query:
            return cls.objects.none()
        matches = cls.objects.filter(query)
        if exclude_pk:
            matches = matches.exclude(pk=exclude_pk)
        return matches.distinct()[:5]

    @classmethod
    def search(cls, text):
        text = (text or "").strip()
        if not text:
            return cls.objects.all()
        results = cls.objects.all()
        # Every word must match something, so "wanjiru kamau" narrows to one person.
        for term in text.split():
            term_query = (
                Q(hospital_number__icontains=term)
                | Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(id_number__icontains=term)
                | Q(legacy_records__legacy_number__iexact=term)
            )
            phone = normalize_phone(term)
            if len(phone) >= 5:
                term_query |= Q(phone__contains=phone)
            results = results.filter(term_query)
        return results.distinct()
