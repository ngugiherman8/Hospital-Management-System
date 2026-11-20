from django.conf import settings
from django.db import models, transaction


class AuditedModel(models.Model):
    """Base for records that must be traceable. Changes are logged automatically."""

    audit_exclude = ()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Append-only record of who did what. Rows cannot be edited or deleted."""

    ACTIONS = [
        ("create", "Created"),
        ("update", "Changed"),
        ("delete", "Deleted"),
        ("view", "Viewed"),
        ("denied", "Refused"),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS)
    model_label = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=200)
    changes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["model_label", "object_id"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.model_label} {self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Audit log entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")


class Sequence(models.Model):
    """Gap-free counters, for example hospital numbers and daily queue numbers."""

    name = models.CharField(max_length=100, unique=True)
    value = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return f"{self.name}: {self.value}"

    @classmethod
    def next(cls, name):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(name=name)
            seq.value += 1
            seq.save(update_fields=["value"])
            return seq.value
