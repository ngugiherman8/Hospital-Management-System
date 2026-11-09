from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .audit import AuditedModel  # noqa: E402,F401  (the instructions import it from here)


class Department(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.SlugField(max_length=40, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    RECEPTIONIST = "receptionist", "Receptionist"
    DOCTOR = "doctor", "Doctor"
    NURSE = "nurse", "Nurse"
    LAB = "lab", "Lab staff"
    PHARMACIST = "pharmacist", "Pharmacist"
    CASHIER = "cashier", "Cashier"


class User(AbstractUser):
    """Staff user. One role plus a department scope."""

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPTIONIST)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff")
    phone = models.CharField(max_length=20, blank=True)
    two_factor_required = models.BooleanField(
        default=False, help_text="Ask this user for a one-time code at sign-in (always on for administrators)."
    )
    totp_secret = models.CharField(max_length=64, blank=True, editable=False)
    totp_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)

    def has_role(self, *roles):
        return self.is_superuser or self.role == Role.ADMIN or self.role in roles

    @property
    def is_clinician(self):
        return self.role in (Role.DOCTOR, Role.NURSE)

    def __str__(self):
        return self.get_full_name() or self.username


class Sequence(models.Model):
    """Gap-free counters (order numbers, admission numbers). Always call inside the caller's transaction so a
    rolled-back save does not burn a number."""

    name = models.CharField(max_length=60, unique=True)
    last_value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next(cls, name: str) -> int:
        from django.db import IntegrityError, transaction

        for attempt in range(2):
            try:
                with transaction.atomic():
                    seq, _ = cls.objects.select_for_update().get_or_create(name=name)
                    seq.last_value += 1
                    seq.save(update_fields=["last_value"])
                    return seq.last_value
            except IntegrityError:  # two requests created the row at once; the second retries
                if attempt:
                    raise

    @classmethod
    def next_number(cls, prefix: str) -> str:
        """e.g. LAB-2026-000123, restarting each year."""
        from django.utils import timezone

        year = timezone.localdate().year
        return f"{prefix}-{year}-{cls.next(f'{prefix}-{year}'):06d}"

    def __str__(self):
        return f"{self.name}: {self.last_value}"


class LoginEvent(models.Model):
    """Login history: successful logins, logouts and failed attempts."""

    class Kind(models.TextChoices):
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        FAILED = "failed", "Failed attempt"

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="login_events")
    username = models.CharField(max_length=150, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


class AuditLog(models.Model):
    """Append-only record of who did what to which record, and when."""

    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        DELETE = "delete", "Deleted"
        VIEW = "view", "Viewed"
        PRINT = "print", "Printed"
        BREAK_GLASS = "break_glass", "Emergency override"

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    action = models.CharField(max_length=12, choices=Action.choices, db_index=True)
    content_type = models.ForeignKey(ContentType, null=True, on_delete=models.SET_NULL)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.get("force_insert"):
            # Only the review fields may change after creation.
            allowed = {"needs_review", "reviewed_by"}
            fields = kwargs.get("update_fields")
            if fields is None or not set(fields) <= allowed:
                raise PermissionError("Audit entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit entries cannot be deleted.")

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.user} {self.action} {self.object_repr}"
