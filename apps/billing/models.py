from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.core.appendonly import AppendOnlyModel
from apps.core.models import AuditedModel, Sequence
from apps.dental.models import DentalProcedure
from apps.orders.models import TestCatalogue
from apps.patients.models import Patient
from apps.pharmacy_rx.models import Drug

MONEY = dict(max_digits=12, decimal_places=2)
ZERO = Decimal("0.00")
PayerKind = Patient.Payer


class ServiceItem(AuditedModel):
    """Something the hospital charges for. Links to the catalogue entry it prices, when there is one."""

    class Category(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        CONSULTATION = "consultation", "Consultation"
        LAB = "lab", "Laboratory test"
        IMAGING = "imaging", "Imaging"
        DENTAL = "dental", "Dental procedure"
        BED = "bed", "Bed day"
        DRUG = "drug", "Drug"
        ANTENATAL = "antenatal", "Antenatal or postnatal contact"
        OTHER = "other", "Other"

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=14, choices=Category.choices)
    test = models.OneToOneField(TestCatalogue, null=True, blank=True, on_delete=models.PROTECT, related_name="service")
    drug = models.OneToOneField(Drug, null=True, blank=True, on_delete=models.PROTECT, related_name="service")
    dental_procedure = models.OneToOneField(
        DentalProcedure, null=True, blank=True, on_delete=models.PROTECT, related_name="service"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def price_for(self, payer_kind):
        """The price for this payer type; self-pay price when none is set for the payer. None if unpriced."""
        prices = {p.payer_kind: p.amount for p in self.prices.all()}
        if payer_kind in prices:
            return prices[payer_kind]
        return prices.get(PayerKind.CASH)


class ItemPrice(AuditedModel):
    service = models.ForeignKey(ServiceItem, on_delete=models.CASCADE, related_name="prices")
    payer_kind = models.CharField(max_length=20, choices=PayerKind.choices)
    amount = models.DecimalField(**MONEY)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["service", "payer_kind"], name="one_price_per_payer")]

    def __str__(self):
        return f"{self.service.name}: {self.get_payer_kind_display()} {self.amount}"

    def clean(self):
        if self.amount is not None and self.amount < 0:
            raise ValidationError("A price cannot be negative.")


class Invoice(AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        PART_PAID = "part_paid", "Part paid"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    number = models.CharField(max_length=30, blank=True, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="invoices")
    appointment = models.ForeignKey(
        Appointment, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    payer_kind = models.CharField(max_length=20, choices=PayerKind.choices, default=PayerKind.CASH)
    payer_name = models.CharField(max_length=150, blank=True)
    member_number = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    finalised_at = models.DateTimeField(null=True, blank=True, editable=False)
    finalised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    void_reason = models.CharField(max_length=300, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_invoice_number")
        ]

    def __str__(self):
        return self.number or f"Draft {self.pk}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Invoices cannot be deleted. Void the invoice instead.")

    @property
    def active_lines(self):
        return self.lines.filter(removed_at__isnull=True)

    @property
    def total(self):
        return self.active_lines.aggregate(t=Sum("amount"))["t"] or ZERO

    @property
    def paid(self):
        """Money received minus money refunded."""
        return self.payments.aggregate(t=Sum("amount"))["t"] or ZERO

    @property
    def balance(self):
        return self.total - self.paid

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    def refresh_status(self):
        """Set open, part-paid or paid from the lines and payments. Draft and void are left alone."""
        if self.status in (self.Status.DRAFT, self.Status.VOID):
            return
        paid, total = self.paid, self.total
        if paid <= 0:
            new = self.Status.OPEN
        elif paid < total:
            new = self.Status.PART_PAID
        else:
            new = self.Status.PAID
        if new != self.status:
            self.status = new
            self.save(update_fields=["status", "updated_at"])


class InvoiceLine(AuditedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    service = models.ForeignKey(ServiceItem, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(**MONEY)
    amount = models.DecimalField(**MONEY, editable=False)
    # Set by automatic charges so the same event is never charged twice.
    source_key = models.CharField(max_length=80, null=True, blank=True, unique=True, editable=False)
    removed_at = models.DateTimeField(null=True, blank=True, editable=False)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    removal_reason = models.CharField(max_length=200, blank=True, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    def save(self, *args, **kwargs):
        if self._state.adding and self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Lines can only be added to a draft invoice.")
        self.amount = (self.unit_price * self.quantity).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Invoice lines cannot be deleted. Remove the line before finalising instead.")


class ChargeAlert(AuditedModel):
    """A charge the system could not make by itself, for the cashier to look at."""

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="+")
    appointment = models.ForeignKey(Appointment, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    source_key = models.CharField(max_length=80)
    message = models.CharField(max_length=300)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.message


class CashUp(AuditedModel):
    """A cashier's end-of-day count against what the system says was taken."""

    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_ups")
    day = models.DateField()
    expected = models.JSONField(default=dict, help_text="Method -> amount the system expects.")
    counted = models.JSONField(default=dict, help_text="Method -> amount counted.")
    difference = models.DecimalField(**MONEY, default=ZERO)
    explanation = models.CharField(max_length=300, blank=True)
    signed_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-day", "cashier_id"]
        constraints = [models.UniqueConstraint(fields=["cashier", "day"], name="one_cash_up_per_cashier_day")]

    def __str__(self):
        return f"Cash-up {self.day} {self.cashier}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Cash-ups cannot be deleted.")


class Payment(AppendOnlyModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MPESA = "mpesa", "M-Pesa"
        CARD = "card", "Card"
        INSURER = "insurer", "Insurer or SHA"
        DEPOSIT = "deposit", "Deposit"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = models.DecimalField(**MONEY, help_text="Negative for a refund.")
    mpesa_code = models.CharField(max_length=20, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text="Card slip number, cheque or remittance.")
    receipt_number = models.CharField(max_length=30, unique=True, editable=False)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            # A repeated M-Pesa code (typed twice, or a repeated callback) cannot create a second payment.
            models.UniqueConstraint(
                fields=["mpesa_code"], condition=~Q(mpesa_code="") & Q(amount__gt=0), name="unique_mpesa_code"
            ),
            models.CheckConstraint(condition=~Q(amount=0), name="payment_not_zero"),
        ]

    def __str__(self):
        return f"{self.receipt_number} {self.amount}"

    @property
    def is_refund(self):
        return self.amount < 0

    def save(self, *args, **kwargs):
        if self._state.adding and not self.receipt_number:
            year = timezone.localdate().year
            prefix = "RF" if self.amount < 0 else "RC"
            self.receipt_number = f"{prefix}-{year}-{Sequence.next(f'receipt-{prefix}-{year}'):06d}"
        super().save(*args, **kwargs)


class Claim(AuditedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PAID = "paid", "Paid"

    number = models.CharField(max_length=30, unique=True, editable=False)
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="claim")
    payer_kind = models.CharField(max_length=20, choices=PayerKind.choices)
    payer_name = models.CharField(max_length=150, blank=True)
    member_number = models.CharField(max_length=60, blank=True)
    diagnoses = models.CharField(max_length=300, blank=True, help_text="ICD-10 codes and names.")
    batch = models.CharField(max_length=7, db_index=True, help_text="Month of the visit, YYYY-MM.")
    amount_claimed = models.DecimalField(**MONEY, editable=False)
    amount_approved = models.DecimalField(**MONEY, null=True, blank=True)
    patient_portion = models.DecimalField(
        **MONEY, default=ZERO, help_text="The patient's co-pay. It is not part of the amount claimed from the payer."
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    last_followed_up_at = models.DateTimeField(null=True, blank=True, editable=False)
    resubmissions = models.PositiveSmallIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.number

    def delete(self, *args, **kwargs):
        raise PermissionError("Claims cannot be deleted.")

    def save(self, *args, **kwargs):
        if not self.number:
            year = timezone.localdate().year
            self.number = f"CLM-{year}-{Sequence.next(f'claim-{year}'):06d}"
        super().save(*args, **kwargs)


class ClaimLine(AuditedModel):
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(**MONEY)
    amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.description

    def delete(self, *args, **kwargs):
        raise PermissionError("Claim lines cannot be deleted.")


class MpesaPrompt(AuditedModel):
    """An M-Pesa payment request (STK Push) sent to a patient's phone. The payment is recorded when M-Pesa confirms."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for the patient"
        SUCCESS = "success", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled by the patient"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="mpesa_prompts")
    phone = models.CharField(max_length=15)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    checkout_request_id = models.CharField(max_length=64, blank=True, db_index=True)
    merchant_request_id = models.CharField(max_length=64, blank=True)
    result_desc = models.CharField(max_length=200, blank=True)
    mpesa_code = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"M-Pesa prompt {self.pk} for {self.invoice}"

    def delete(self, *args, **kwargs):
        raise PermissionError("M-Pesa prompts cannot be deleted.")
