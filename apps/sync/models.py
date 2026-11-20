from django.db import models
from django.utils import timezone


class Outbox(models.Model):
    """One row per change to a record, written in the same database transaction as the change itself.

    The sender ships rows to the cloud in `id` order. A row is only marked sent once the cloud has applied it.
    """

    model_label = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    action = models.CharField(max_length=6, default="save")  # save or delete
    data = models.JSONField(null=True)
    txid = models.BigIntegerField(default=0, help_text="Database transaction. Rows of one transaction travel together.")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["model_label", "object_id", "version"], name="one_outbox_version")
        ]

    def __str__(self):
        return f"{self.model_label} {self.object_id} v{self.version}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Outbox rows are kept.")


class AppliedChange(models.Model):
    """Cloud side: which changes have been applied, so a repeat is harmless."""

    model_label = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    applied = models.BooleanField(default=True, help_text="False when a newer version had already arrived.")
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["model_label", "object_id", "version"], name="one_applied_version")
        ]
        indexes = [models.Index(fields=["model_label", "object_id"])]

    def __str__(self):
        return f"{self.model_label} {self.object_id} v{self.version}"
