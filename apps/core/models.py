from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.core.audit import AuditedModel
#from apps.core.models import Sequence

class Sequence(models.Model):
    name = models.CharField(max_length=60, unique=True)
    last_value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next_number(cls, name):
        with transaction.atomic():
            sequence, created = cls.objects.select_for_update().get_or_create(
                name=name
            )

            sequence.last_value += 1
            sequence.save(update_fields=["last_value"])

            return sequence.last_value
class TestCatalogue(models.Model):
    """A test or procedure that can be ordered."""

    __test__ = False  # not a pytest test class

    class Kind(models.TextChoices):
        LAB = "lab", "Laboratory"
        RADIOLOGY = "radiology", "Radiology"
        PROCEDURE = "procedure", "Procedure"

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.LAB)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Placeholder until Billing")
    normal_range = models.CharField(max_length=120, blank=True)
    sample_type = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name


class Order(AuditedModel):
    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        IN_PROGRESS = "in_progress", "In progress"
        RESULTED = "resulted", "Result in"
        CANCELLED = "cancelled", "Cancelled"

    PREFIX = {"lab": "LAB", "radiology": "RAD", "procedure": "PRC"}

    number = models.CharField(max_length=24, unique=True, editable=False)
    visit = models.ForeignKey("clinical.Visit", on_delete=models.CASCADE, related_name="orders")
    test = models.ForeignKey(TestCatalogue, on_delete=models.PROTECT)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    ordered_at = models.DateTimeField(default=timezone.now)
    urgent = models.BooleanField(default=False)
    clinical_notes = models.CharField(max_length=200, blank=True, help_text="Reason or question for the lab")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ORDERED, db_index=True)
    sample_taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["ordered_at", "id"]

    def save(self, *args, **kwargs):
        if self._state.adding and not self.number:
            with transaction.atomic():  # keeps the number gap-free if the save fails
                self.number = Sequence.next_number(self.PREFIX[self.test.kind])
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.status in (self.Status.ORDERED, self.Status.IN_PROGRESS)

    @property
    def current_result(self):
        return self.results.filter(corrected_by__isnull=True).order_by("-id").first()

    def __str__(self):
        return f"{self.number} {self.test.name}"


class Result(AuditedModel):
    """Results are never edited. A correction is a new row pointing at the one it replaces."""

    class Flag(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        LOW = "low", "Low"
        ABNORMAL = "abnormal", "Abnormal"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="results")
    value = models.CharField(max_length=200)
    unit = models.CharField(max_length=30, blank=True)
    flag = models.CharField(max_length=10, choices=Flag.choices)
    comment = models.TextField(blank=True)
    resulted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    resulted_at = models.DateTimeField(default=timezone.now)
    corrects = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrected_by")

    class Meta:
        ordering = ["resulted_at", "id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Results cannot be edited. Enter a correction instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Results cannot be deleted.")

    @property
    def earlier(self):
        out, node = [], self.corrects
        while node:
            out.append(node)
            node = node.corrects
        return out

    def __str__(self):
        return f"{self.order.test.name}: {self.value} {self.unit}".strip()
