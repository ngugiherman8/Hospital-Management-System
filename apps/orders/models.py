from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.core.models import AuditedModel, Sequence


class TestCatalogue(AuditedModel):
    __test__ = False  # not a pytest class

    class Kind(models.TextChoices):
        LAB = "lab", "Laboratory"
        IMAGING = "imaging", "Imaging"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LAB)
    sample_type = models.CharField(max_length=60, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    ref_low = models.DecimalField("Normal low", max_digits=9, decimal_places=2, null=True, blank=True)
    ref_high = models.DecimalField("Normal high", max_digits=9, decimal_places=2, null=True, blank=True)
    normal_range = models.CharField(max_length=100, blank=True, help_text="Shown to staff, e.g. Negative")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name


class Order(AuditedModel):
    """A set of tests of one kind ordered together for a visit."""

    appointment = models.ForeignKey(Appointment, on_delete=models.PROTECT, related_name="orders")
    kind = models.CharField(max_length=10, choices=TestCatalogue.Kind.choices, default=TestCatalogue.Kind.LAB)
    number = models.CharField(max_length=30, unique=True, editable=False)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField("Clinical note for the lab", max_length=300, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            year = timezone.localdate().year
            prefix = "LAB" if self.kind == TestCatalogue.Kind.LAB else "IMG"
            self.number = f"{prefix}-{year}-{Sequence.next(f'order-{prefix}-{year}'):06d}"
        super().save(*args, **kwargs)

    @property
    def status(self):
        statuses = {i.status for i in self.items.all()}
        if not statuses or statuses == {OrderItem.Status.CANCELLED}:
            return OrderItem.Status.CANCELLED
        live = statuses - {OrderItem.Status.CANCELLED}
        if live == {OrderItem.Status.RESULT_IN}:
            return OrderItem.Status.RESULT_IN
        for s in (OrderItem.Status.IN_PROGRESS, OrderItem.Status.SAMPLE_TAKEN):
            if s in live:
                return s
        return OrderItem.Status.ORDERED


class OrderItem(AuditedModel):
    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        SAMPLE_TAKEN = "sample_taken", "Sample taken"
        IN_PROGRESS = "in_progress", "In progress"
        RESULT_IN = "result_in", "Result in"
        CANCELLED = "cancelled", "Cancelled"

    PENDING = (Status.ORDERED, Status.SAMPLE_TAKEN, Status.IN_PROGRESS)

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="items")
    test = models.ForeignKey(TestCatalogue, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ORDERED, db_index=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.number}: {self.test.name}"

    @property
    def current_result(self):
        return self.results.filter(replaced_by__isnull=True).first()


class Result(AuditedModel):
    """A test result. Never edited: a correction is a new result that replaces the old one."""

    class Flag(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        LOW = "low", "Low"

    item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="results")
    value = models.CharField(max_length=200)
    unit = models.CharField(max_length=30, blank=True)
    flag = models.CharField(max_length=10, choices=Flag.choices, default=Flag.NORMAL)
    comment = models.CharField(max_length=300, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    entered_at = models.DateTimeField(default=timezone.now, editable=False)
    replaces = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="replaced_by", editable=False
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.item.test.name}: {self.value}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Saved results cannot be changed; enter a correction.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Saved results cannot be deleted.")

    @staticmethod
    def auto_flag(test, value):
        """Flag a numeric value against the test's normal range, or None when it cannot be judged."""
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
        if test.ref_low is not None and number < test.ref_low:
            return Result.Flag.LOW
        if test.ref_high is not None and number > test.ref_high:
            return Result.Flag.HIGH
        if test.ref_low is not None or test.ref_high is not None:
            return Result.Flag.NORMAL
        return None


def record_result(item, value, user, unit="", flag="", comment=""):
    """Save a result for an item (or a correction when it already has one) and mark the item done."""
    from django.db import transaction

    with transaction.atomic():
        item = OrderItem.objects.select_for_update().get(pk=item.pk)
        if item.status == OrderItem.Status.CANCELLED:
            raise ValueError("This test was cancelled.")
        previous = item.current_result
        flag = flag or Result.auto_flag(item.test, value) or Result.Flag.NORMAL
        result = Result.objects.create(
            item=item,
            value=value,
            unit=unit or item.test.unit,
            flag=flag,
            comment=comment,
            entered_by=user,
            replaces=previous,
        )
        item.status = OrderItem.Status.RESULT_IN
        item.save()
        return result
