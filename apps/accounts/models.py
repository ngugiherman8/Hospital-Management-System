import datetime

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from apps.core.models import AuditedModel


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    RECEPTION = "reception", "Receptionist"
    NURSE = "nurse", "Nurse"
    DOCTOR = "doctor", "Doctor"
    LAB = "lab", "Lab technician"
    PHARMACIST = "pharmacist", "Pharmacist"
    CASHIER = "cashier", "Cashier"
    DENTIST = "dentist", "Dentist"
    MIDWIFE = "midwife", "Midwife"
    RADIOGRAPHER = "radiographer", "Radiographer"
    RADIOLOGIST = "radiologist", "Radiologist"
    MANAGER = "manager", "Manager (reports only)"
    HR = "hr", "HR and payroll officer"
    STOREKEEPER = "storekeeper", "Storekeeper"


# Staff who run a consultation: notes, diagnoses, orders. Dentists and midwives only in their own clinic.
CLINICIAN_ROLES = (Role.DOCTOR, Role.DENTIST, Role.MIDWIFE)


class User(AuditedModel, AbstractUser):
    audit_exclude = ("password", "last_login")

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPTION)
    department = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(
        default=False, help_text="Set for new accounts: the person chooses their own password at first sign-in."
    )

    def has_role(self, *roles):
        """True for superusers, administrators and anyone holding one of `roles`."""
        if self.is_superuser or self.role == Role.ADMIN:
            return True
        return self.role in roles

    def __str__(self):
        return self.get_full_name() or self.username


class TOTPDevice(AuditedModel):
    """An authenticator app linked to a user. Required for administrators."""

    audit_exclude = ("secret",)

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_step = models.BigIntegerField(default=0, help_text="Last time step used, so a code works only once.")

    def __str__(self):
        return f"Authenticator for {self.user}"


class FailedLogin(models.Model):
    """One wrong password. Too many in a short time lock the account for a while."""

    username = models.CharField(max_length=150, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.username} {self.at:%Y-%m-%d %H:%M}"


def lockout_minutes_left(username, ip=None, now=None):
    """Minutes until sign-in is allowed again, or 0 when not locked."""
    now = now or timezone.now()
    window = datetime.timedelta(minutes=settings.LOCKOUT_MINUTES)
    recent = FailedLogin.objects.filter(at__gte=now - window)
    mine = list(recent.filter(username__iexact=username).order_by("at").values_list("at", flat=True))
    locked_until = None
    if len(mine) >= settings.LOCKOUT_ATTEMPTS:
        locked_until = mine[-settings.LOCKOUT_ATTEMPTS] + window
    if ip:
        from_ip = list(recent.filter(ip_address=ip).order_by("at").values_list("at", flat=True))
        if len(from_ip) >= settings.LOCKOUT_ATTEMPTS_PER_IP:
            until = from_ip[-settings.LOCKOUT_ATTEMPTS_PER_IP] + window
            locked_until = max(locked_until, until) if locked_until else until
    if locked_until and locked_until > now:
        return int((locked_until - now).total_seconds() // 60) + 1
    return 0
