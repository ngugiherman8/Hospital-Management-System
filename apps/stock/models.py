import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.billing.models import MONEY
from apps.core.appendonly import AppendOnlyModel
from apps.core.models import AuditedModel, Sequence
from apps.pharmacy_rx.models import Drug, PrescriptionItem

EXPIRY_WARNING_DAYS = 90


def _number(prefix, name):
    year = timezone.localdate().year
    return f"{prefix}-{year}-{Sequence.next(f'{name}-{year}'):06d}"


class Supplier(AuditedModel):
    name = models.CharField(max_length=150, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent to supplier"
        PART = "part", "Part received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=30, unique=True, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = _number("PO", "purchase-order")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Purchase orders cannot be deleted. Cancel the order instead.")

    def refresh_status(self):
        lines = list(self.lines.all())
        if self.status in (self.Status.DRAFT, self.Status.CANCELLED) or not lines:
            return
        got = {line.pk: line.received for line in lines}
        if all(got[line.pk] >= line.quantity for line in lines):
            new = self.Status.RECEIVED
        elif any(got.values()):
            new = self.Status.PART
        else:
            new = self.Status.SENT
        if new != self.status:
            self.status = new
            self.save(update_fields=["status", "updated_at"])


class PurchaseOrderLine(AuditedModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines")
    drug = models.ForeignKey(Drug, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(**MONEY, default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.drug} x{self.quantity}"

    @property
    def received(self):
        return (
            StockMovement.objects.filter(
                kind=StockMovement.Kind.RECEIVE, batch__receipt__purchase_order=self.order, batch__drug=self.drug
            ).aggregate(t=Sum("quantity"))["t"]
            or 0
        )


class GoodsReceipt(AuditedModel):
    number = models.CharField(max_length=30, unique=True, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="receipts")
    purchase_order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts"
    )
    supplier_invoice = models.CharField(max_length=60, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    received_on = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = _number("GRN", "goods-receipt")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Goods receipts cannot be deleted.")


class StockBatch(AuditedModel):
    drug = models.ForeignKey(Drug, on_delete=models.PROTECT, related_name="batches")
    receipt = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=60)
    expiry_date = models.DateField()
    unit_cost = models.DecimalField(**MONEY, default=0)

    class Meta:
        ordering = ["expiry_date", "id"]
        verbose_name_plural = "stock batches"
        constraints = [
            models.UniqueConstraint(fields=["drug", "batch_number", "expiry_date"], name="unique_stock_batch")
        ]

    def __str__(self):
        return f"{self.drug} batch {self.batch_number}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Batches cannot be deleted. Write the stock off instead.")

    @property
    def quantity(self):
        """Quantity on hand, worked out from the movements. It is never stored."""
        return self.movements.aggregate(t=Sum("quantity"))["t"] or 0

    @property
    def expired(self):
        return self.expiry_date < timezone.localdate()

    @property
    def expires_soon(self):
        today = timezone.localdate()
        return today <= self.expiry_date <= today + datetime.timedelta(days=EXPIRY_WARNING_DAYS)


class StockMovement(AppendOnlyModel):
    class Kind(models.TextChoices):
        RECEIVE = "receive", "Received"
        DISPENSE = "dispense", "Dispensed"
        ADJUST = "adjust", "Adjustment"
        EXPIRED = "expired", "Expired or written off"
        RETURN = "return", "Returned to stock"

    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    quantity = models.IntegerField(help_text="Positive adds stock, negative removes it.")
    reason = models.CharField(max_length=300, blank=True)
    prescription_item = models.ForeignKey(
        PrescriptionItem, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=~Q(quantity=0), name="movement_not_zero")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity} of {self.batch}"

    def save(self, *args, **kwargs):
        if self._state.adding:
            sign_ok = {
                self.Kind.RECEIVE: self.quantity > 0,
                self.Kind.RETURN: self.quantity > 0,
                self.Kind.DISPENSE: self.quantity < 0,
                self.Kind.EXPIRED: self.quantity < 0,
                self.Kind.ADJUST: self.quantity != 0,
            }[self.kind]
            if not sign_ok:
                raise ValidationError("The quantity does not fit the kind of movement.")
            if self.kind in (self.Kind.ADJUST, self.Kind.EXPIRED, self.Kind.RETURN) and not self.reason.strip():
                raise ValidationError("Give a reason for this stock movement.")
        super().save(*args, **kwargs)
