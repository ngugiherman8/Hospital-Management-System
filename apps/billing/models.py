"""Money rules: DecimalField(12, 2) in Kenyan shillings, never floats. Invoices, lines and payments are never
edited or deleted. A mistake is fixed by a new reversing row (void, credit note, refund) with a reason and a name."""

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.core.audit import AuditedModel
from apps.core.models import Sequence
from apps.patients.models import PayerType

MONEY = {"max_digits": 12, "decimal_places": 2}


class Payer(AuditedModel):
    """Who pays: cash, SHA, or a named insurer or company."""

    name = models.CharField(max_length=120, unique=True)
    payer_type = models.CharField(max_length=12, choices=PayerType.choices, default=PayerType.CASH)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    @classmethod
    def for_cover(cls, cover):
        """The payer for a patient's primary cover (created on first use)."""
        ptype = cover.payer_type if cover else PayerType.CASH
        if ptype == PayerType.CASH:
            name = "Cash / M-Pesa"
        elif ptype == PayerType.SHA:
            name = "SHA"
        else:
            name = (cover.insurer_name or dict(PayerType.choices)[ptype]).strip()
        payer, _ = cls.objects.get_or_create(name=name, defaults={"payer_type": ptype})
        return payer

    def __str__(self):
        return self.name


class ServiceItem(AuditedModel):
    class Category(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        CONSULTATION = "consultation", "Consultation"
        LAB = "lab", "Laboratory test"
        IMAGING = "imaging", "Imaging"
        DRUG = "drug", "Medicine"
        BED_DAY = "bed_day", "Bed day"
        PROCEDURE = "procedure", "Procedure"
        DENTAL = "dental", "Dental"
        OTHER = "other", "Other"

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=14, choices=Category.choices, default=Category.OTHER)
    test = models.OneToOneField(
        "orders.TestCatalogue", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    drug = models.OneToOneField("pharmacy_rx.Drug", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def price_for(self, payer_type):
        """Price for this payer type, falling back to the cash price. None when no price is set."""
        prices = {p.payer_type: p.amount for p in self.prices.all()}
        if payer_type in prices:
            return prices[payer_type]
        return prices.get(PayerType.CASH)

    def __str__(self):
        return f"{self.code} {self.name}"


class Price(AuditedModel):
    service = models.ForeignKey(ServiceItem, on_delete=models.CASCADE, related_name="prices")
    payer_type = models.CharField(max_length=12, choices=PayerType.choices)
    amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["service", "payer_type"]
        constraints = [
            models.UniqueConstraint(fields=["service", "payer_type"], name="one_price_per_service_and_payer_type"),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="price_not_negative"),
        ]

    def __str__(self):
        return f"{self.service.code} {self.get_payer_type_display()}: {self.amount}"


class Invoice(AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        PART_PAID = "part_paid", "Part paid"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    number = models.CharField(max_length=24, unique=True, editable=False)
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="invoices")
    visit = models.ForeignKey(
        "clinical.Visit", null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    payer = models.ForeignKey(Payer, on_delete=models.PROTECT, related_name="invoices")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    finalised_at = models.DateTimeField(null=True, blank=True)
    finalised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    void_reason = models.CharField(max_length=200, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self._state.adding and not self.number:
            with transaction.atomic():
                self.number = Sequence.next_number("INV")
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Invoices cannot be deleted. Void them instead.")

    # amounts are always worked out from the rows, never stored, so they cannot drift
    @property
    def active_lines(self):
        return self.lines.filter(is_removed=False)

    @property
    def total(self) -> Decimal:
        return self.active_lines.aggregate(t=models.Sum("amount"))["t"] or Decimal("0.00")

    @property
    def paid(self) -> Decimal:
        """Net of refunds."""
        return self.payments.aggregate(t=models.Sum("amount"))["t"] or Decimal("0.00")

    @property
    def balance(self) -> Decimal:
        return self.total - self.paid

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def is_void(self):
        return self.status == self.Status.VOID

    @property
    def has_missing_prices(self):
        return self.active_lines.filter(price_missing=True).exists()

    def refresh_status(self):
        """Open, part-paid or paid follows the payments. Draft and void are set deliberately."""
        if self.status in (self.Status.DRAFT, self.Status.VOID):
            return
        total, paid = self.total, self.paid
        if paid >= total:
            new = self.Status.PAID
        elif paid > 0:
            new = self.Status.PART_PAID
        else:
            new = self.Status.OPEN
        if new != self.status:
            self.status = new
            self.save(update_fields=["status"])

    def __str__(self):
        return f"{self.number} {self.patient.full_name}"


class InvoiceLine(AuditedModel):
    class Kind(models.TextChoices):
        CHARGE = "charge", "Charge"
        ADJUSTMENT = "adjustment", "Credit note"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    service = models.ForeignKey(ServiceItem, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price = models.DecimalField(**MONEY)
    amount = models.DecimalField(**MONEY, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CHARGE)
    source_key = models.CharField(max_length=60, blank=True, help_text="Which clinical event created this charge")
    price_missing = models.BooleanField(default=False)
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    is_removed = models.BooleanField(default=False)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    FROZEN = ("invoice_id", "service_id", "description", "quantity", "kind", "source_key")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            # one charge per clinical event, ever, so syncing again can never double-charge
            models.UniqueConstraint(
                fields=["source_key"], condition=~models.Q(source_key=""), name="one_line_per_event"
            ),
        ]

    def save(self, *args, **kwargs):
        self.amount = (Decimal(self.quantity) * Decimal(self.unit_price)).quantize(Decimal("0.01"))
        if not self._state.adding:
            old = InvoiceLine.objects.get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f) for f in self.FROZEN):
                raise PermissionError("Invoice lines cannot be edited. Remove the line or add a credit note.")
            price_changed = old.unit_price != self.unit_price
            if price_changed and not old.price_missing:
                raise PermissionError("A priced invoice line cannot be edited. Remove the line or add a credit note.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Invoice lines cannot be deleted.")

    def __str__(self):
        return f"{self.description}: {self.amount}"


class Payment(AuditedModel):
    """Append only. A refund is a negative payment approved by an administrator."""

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MPESA = "mpesa", "M-Pesa"
        CARD = "card", "Card"
        INSURER = "insurer", "Insurer"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=8, choices=Method.choices)
    amount = models.DecimalField(**MONEY)
    is_deposit = models.BooleanField(default=False, help_text="Taken in advance, for example an inpatient deposit")
    mpesa_code = models.CharField(max_length=20, blank=True)
    external_id = models.CharField(
        max_length=80, blank=True, help_text="Provider's id, so a repeat callback is ignored"
    )
    reference = models.CharField(max_length=120, blank=True)
    receipt_number = models.CharField(max_length=24, unique=True, editable=False)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    refund_reason = models.CharField(max_length=200, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["received_at", "id"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(amount=0), name="payment_not_zero"),
            models.UniqueConstraint(
                fields=["mpesa_code"], condition=~models.Q(mpesa_code=""), name="one_payment_per_mpesa_code"
            ),
            models.UniqueConstraint(
                fields=["external_id"], condition=~models.Q(external_id=""), name="one_payment_per_provider_id"
            ),
        ]

    @property
    def is_refund(self):
        return self.amount < 0

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Payments cannot be edited. Record a refund instead.")
        if not self.receipt_number:
            with transaction.atomic():
                self.receipt_number = Sequence.next_number("RFD" if self.amount < 0 else "RCT")
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Payments cannot be deleted. Record a refund instead.")

    def __str__(self):
        return f"{self.receipt_number} {self.amount}"


class MpesaRequest(AuditedModel):
    """An M-Pesa payment prompt sent to a patient's phone, waiting for the provider's callback."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for the patient"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed or cancelled"

    checkout_request_id = models.CharField(max_length=80, unique=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="mpesa_requests")
    amount = models.DecimalField(**MONEY)
    phone = models.CharField(max_length=15)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    result_desc = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.checkout_request_id} {self.status}"
