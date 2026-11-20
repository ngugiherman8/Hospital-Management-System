from django.db import models
from django.utils import timezone


class CheckState(models.Model):
    """Remembers which health checks are failing, so IT is alerted once and again only as a reminder."""

    name = models.CharField(max_length=40, unique=True)
    failing_since = models.DateTimeField(null=True, blank=True)
    last_alerted_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.name}: {'FAILING' if self.failing_since else 'ok'}"
