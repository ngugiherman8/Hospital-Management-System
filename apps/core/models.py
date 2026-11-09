from django.contrib.auth.models import AbstractUser
from django.db import models, transaction

from apps.core.audit import AuditedModel  # noqa: F401  (re-exported: `from apps.core.models import AuditedModel`)


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    RECEPTIONIST = "receptionist", "Receptionist"
    DOCTOR = "doctor", "Doctor"
    NURSE = "nurse", "Nurse"
    LAB = "lab", "Lab staff"
    PHARMACIST = "pharmacist", "Pharmacist"
    CASHIER = "cashier", "Cashier"


class Department(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.SlugField(max_length=40, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.RECEPTIONIST)
    phone = models.CharField(max_length=20, blank=True)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff"
    )
    two_factor_required = models.BooleanField(
        default=False,
        help_text="Ask this user for a one-time code at sign-in (always on for administrators).",
    )
    totp_secret = models.CharField(max_length=64, blank=True, editable=False)
    totp_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)

    def has_role(self, *roles):
        """True for superusers, or when the user's role is one of `roles`."""
        return self.is_superuser or self.role in roles

    def __str__(self):
        return self.get_full_name() or self.username


class LoginEvent(models.Model):
    class Kind(models.TextChoices):
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        FAILED = "failed", "Failed attempt"

    user = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="login_events"
    )
    username = models.CharField(max_length=150, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        DELETE = "delete", "Deleted"
        VIEW = "view", "Viewed"
        PRINT = "print", "Printed"
        BREAK_GLASS = "break_glass", "Emergency override"

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    action = models.CharField(max_length=12, choices=Action.choices, db_index=True)
    content_type = models.ForeignKey("contenttypes.ContentType", null=True, on_delete=models.SET_NULL)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    reviewed_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="core_auditl_content_fec0c4_idx")]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.object_repr}"


class Sequence(models.Model):
    name = models.CharField(max_length=60, unique=True)
    last_value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next_number(cls, name):
        with transaction.atomic():
            sequence, _ = cls.objects.select_for_update().get_or_create(name=name)
            sequence.last_value += 1
            sequence.save(update_fields=["last_value"])
            return sequence.last_value
