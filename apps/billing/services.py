"""All money movements go through here, so the rules live in one place."""

import csv
import datetime
import io
import logging
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Role

from .models import (
    ZERO,
    CashUp,
    ChargeAlert,
    Claim,
    ClaimLine,
    Invoice,
    InvoiceLine,
    PayerKind,
    Payment,
    ServiceItem,
)

log = logging.getLogger(__name__)

CLAIM_PAYERS = (PayerKind.SHA, PayerKind.INSURER, PayerKind.CORPORATE)
CASHUP_METHODS = (Payment.Method.CASH, Payment.Method.MPESA, Payment.Method.CARD, Payment.Method.DEPOSIT)


def _money(value):
    return Decimal(value).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- charges


def draft_invoice_for(patient, appointment=None, user=None):
    """The draft invoice charges go onto, created when there is none. Finalised invoices are never added to."""
    draft = Invoice.objects.filter(patient=patient, appointment=appointment, status=Invoice.Status.DRAFT).first()
    if draft:
        return draft
    return Invoice.objects.create(
        patient=patient,
        appointment=appointment,
        payer_kind=patient.payer,
        payer_name=patient.payer_name,
        member_number=patient.member_number,
        created_by=user,
    )


def post_charge(patient, service, quantity=1, appointment=None, source_key=None, description=None, user=None):
    """Add one charge to the visit's draft invoice. The same source_key is only ever charged once.

    Returns the line, or None when it was already charged or has no price (a ChargeAlert is raised then).
    """
    if source_key and InvoiceLine.objects.filter(source_key=source_key).exists():
        return None
    if service is None or not service.is_active:
        _alert(patient, appointment, source_key, "No price list entry for this service, add the charge by hand.")
        return None
    with transaction.atomic():
        invoice = draft_invoice_for(patient, appointment, user)
        price = service.price_for(invoice.payer_kind)
        if price is None:
            _alert(patient, appointment, source_key, f"{service.name} has no price. Ask the administrator to set it.")
            return None
        try:
            with transaction.atomic():
                return InvoiceLine.objects.create(
                    invoice=invoice,
                    service=service,
                    description=description or service.name,
                    quantity=quantity,
                    unit_price=price,
                    source_key=source_key,
                )
        except IntegrityError:  # another process charged the same event first
            return None


def _alert(patient, appointment, source_key, message):
    log.warning("Charge not made (%s): %s", source_key, message)
    ChargeAlert.objects.get_or_create(
        patient=patient,
        appointment=appointment,
        source_key=source_key or "",
        resolved_at=None,
        defaults={"message": message},
    )


def withdraw_charge(source_key, reason):
    """A charged event was cancelled. Remove the line while the invoice is a draft, otherwise raise an alert."""
    line = InvoiceLine.objects.select_related("invoice__patient").filter(source_key=source_key).first()
    if line is None or line.removed_at:
        return
    if line.invoice.status == Invoice.Status.DRAFT:
        line.removed_at = timezone.now()
        line.removal_reason = reason
        line.save()
    else:
        _alert(
            line.invoice.patient,
            line.invoice.appointment,
            f"withdraw:{source_key}",
            f"{line.description} was cancelled after invoice {line.invoice} was finalised. Review and refund if due.",
        )


def service_by_code(code):
    return ServiceItem.objects.filter(code=code, is_active=True).first()


def service_for_drug(drug):
    """The billable item for a drug. A drug added after the last sync gets one made on the spot, with no price,
    so dispensing it raises a visible ChargeAlert ("has no price") instead of being given away unrecorded."""
    item, _ = ServiceItem.objects.get_or_create(
        drug=drug,
        defaults={"code": f"D-{drug.pk}", "name": str(drug), "category": ServiceItem.Category.DRUG},
    )
    return item if item.is_active else None


def sync_service_items():
    """Make sure every drug, test and dental procedure has a billable item (safe to run again).

    New items have NO price. Set prices in Administration; until then a charge raises an alert rather than
    billing a guessed amount. Returns how many items were created.
    """
    from apps.dental.models import DentalProcedure
    from apps.orders.models import TestCatalogue
    from apps.pharmacy_rx.models import Drug

    Category = ServiceItem.Category
    made = 0
    for drug in Drug.objects.filter(service__isnull=True):
        _, new = ServiceItem.objects.get_or_create(
            drug=drug, defaults={"code": f"D-{drug.pk}", "name": str(drug), "category": Category.DRUG}
        )
        made += new
    for test in TestCatalogue.objects.filter(service__isnull=True):
        category = Category.IMAGING if test.kind == "imaging" else Category.LAB
        _, new = ServiceItem.objects.get_or_create(
            test=test, defaults={"code": f"T-{test.code}", "name": test.name, "category": category}
        )
        made += new
    for proc in DentalProcedure.objects.filter(service__isnull=True):
        _, new = ServiceItem.objects.get_or_create(
            dental_procedure=proc, defaults={"code": f"DP-{proc.code}", "name": proc.name, "category": Category.DENTAL}
        )
        made += new
    return made


def charge_bed_nights(admission, today=None):
    """Charge each night of the stay once. Completed nights only; a same-day stay counts as one night."""
    today = today or timezone.localdate()
    start = timezone.localtime(admission.admitted_at).date()
    if admission.discharged_at:
        end = max(timezone.localtime(admission.discharged_at).date(), start + datetime.timedelta(days=1))
    else:
        end = today
    ward_kind = admission.bed.ward.kind
    service = service_by_code(f"BED-{ward_kind.upper()}") or service_by_code("BED-GENERAL")
    made = 0
    day = start
    while day < end:
        line = post_charge(
            admission.patient,
            service,
            appointment=admission.appointment,
            source_key=f"bed:{admission.pk}:{day.isoformat()}",
            description=f"Bed day {day:%d %b %Y} ({admission.bed.ward.name})",
        )
        made += 1 if line else 0
        day += datetime.timedelta(days=1)
    return made


# ---------------------------------------------------------------- invoices


def finalise(invoice, user):
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Only a draft invoice can be finalised.")
        if not invoice.active_lines.exists():
            raise ValidationError("An invoice needs at least one line.")
        from apps.core.models import Sequence

        year = timezone.localdate().year
        invoice.number = f"INV-{year}-{Sequence.next(f'invoice-{year}'):06d}"
        invoice.status = Invoice.Status.OPEN
        invoice.finalised_at = timezone.now()
        invoice.finalised_by = user
        invoice.save()
        invoice.refresh_status()
    return invoice


def add_line(invoice, service, quantity, user):
    if not invoice.is_draft:
        raise ValidationError("Lines can only be added to a draft invoice.")
    price = service.price_for(invoice.payer_kind)
    if price is None:
        raise ValidationError(f"{service.name} has no price.")
    return InvoiceLine.objects.create(
        invoice=invoice, service=service, description=service.name, quantity=quantity, unit_price=price
    )


def remove_line(line, user, reason=""):
    if not line.invoice.is_draft:
        raise ValidationError("Lines can only be removed before the invoice is finalised.")
    if line.removed_at:
        return
    line.removed_at = timezone.now()
    line.removed_by = user
    line.removal_reason = reason or "Removed by cashier"
    line.save()


def void_invoice(invoice, user, reason):
    if not user.has_role(Role.ADMIN):
        raise PermissionError("Only an administrator can void an invoice.")
    if not reason.strip():
        raise ValidationError("Give a reason for voiding the invoice.")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status == Invoice.Status.VOID:
            raise ValidationError("This invoice is already void.")
        if invoice.paid != 0:
            raise ValidationError("Refund the payments on this invoice before voiding it.")
        if hasattr(invoice, "claim") and invoice.claim.status in ("submitted", "approved", "paid"):
            raise ValidationError("This invoice has a claim with the payer. Deal with the claim first.")
        invoice.status = Invoice.Status.VOID
        invoice.void_reason = reason.strip()
        invoice.voided_at = timezone.now()
        invoice.voided_by = user
        invoice.save()
    return invoice


# ---------------------------------------------------------------- payments


def _cashier_closed(user, day):
    return CashUp.objects.filter(cashier=user, day=day).exists()


def take_payment(invoice, method, amount, user, mpesa_code="", reference=""):
    amount = _money(amount)
    if amount <= 0:
        raise ValidationError("The amount must be more than zero.")
    mpesa_code = mpesa_code.strip().upper()
    if method == Payment.Method.MPESA and not mpesa_code:
        raise ValidationError("Type in the M-Pesa transaction code.")
    if method != Payment.Method.MPESA:
        mpesa_code = ""
    if method != Payment.Method.INSURER and _cashier_closed(user, timezone.localdate()):
        raise ValidationError("You have already closed today. Payments after cash-up go in tomorrow's day.")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
            raise ValidationError("Payments can only be taken on a finalised invoice.")
        if amount > invoice.balance:
            raise ValidationError(f"The amount is more than the balance of {invoice.balance}.")
        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    invoice=invoice,
                    method=method,
                    amount=amount,
                    mpesa_code=mpesa_code,
                    reference=reference,
                    received_by=user,
                )
        except IntegrityError:
            raise ValidationError("That M-Pesa code has already been recorded.") from None
        invoice.refresh_status()
    return payment


def record_mpesa_confirmation(invoice_number, code, amount, user=None, reference="Automatic confirmation"):
    """Handle a payment confirmation from M-Pesa. Safe to call twice with the same code.

    Returns (payment, created). The second and later calls with a code return the first payment.
    """
    code = code.strip().upper()
    existing = Payment.objects.filter(mpesa_code=code, amount__gt=0).first()
    if existing:
        return existing, False
    invoice = Invoice.objects.filter(number=invoice_number).first()
    if invoice is None:
        raise ValidationError("Unknown invoice number.")
    amount = _money(amount)
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    invoice=invoice,
                    method=Payment.Method.MPESA,
                    amount=amount,
                    mpesa_code=code,
                    reference=reference,
                    received_by=user,
                )
        except IntegrityError:
            return Payment.objects.get(mpesa_code=code, amount__gt=0), False
        invoice.refresh_status()
    return payment, True


def refund(invoice, amount, user, reason, method=Payment.Method.CASH):
    if not user.has_role(Role.ADMIN):
        raise PermissionError("Only an administrator can approve a refund.")
    amount = _money(amount)
    if amount <= 0:
        raise ValidationError("The refund amount must be more than zero.")
    if not reason.strip():
        raise ValidationError("Give a reason for the refund.")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if amount > invoice.paid:
            raise ValidationError(f"Only {invoice.paid} has been paid on this invoice.")
        payment = Payment.objects.create(
            invoice=invoice,
            method=method,
            amount=-amount,
            reason=reason.strip(),
            received_by=user,
            approved_by=user,
        )
        invoice.refresh_status()
    return payment


# ---------------------------------------------------------------- end of day


def expected_for(cashier, day):
    rows = Payment.objects.filter(received_by=cashier, created_at__date=day, method__in=CASHUP_METHODS)
    out = {}
    for method in CASHUP_METHODS:
        total = rows.filter(method=method).aggregate(t=Sum("amount"))["t"] or ZERO
        out[method.value] = str(_money(total))
    return out


def close_day(cashier, day, counted, explanation=""):
    """Sign off the cashier's day. A difference needs an explanation."""
    if CashUp.objects.filter(cashier=cashier, day=day).exists():
        raise ValidationError("This day is already closed.")
    expected = expected_for(cashier, day)
    counted = {m.value: str(_money(counted.get(m.value, 0))) for m in CASHUP_METHODS}
    difference = sum((Decimal(counted[m]) - Decimal(expected[m]) for m in expected), ZERO)
    if difference != 0 and not explanation.strip():
        raise ValidationError(f"The count is out by {difference}. Write what you think explains it.")
    return CashUp.objects.create(
        cashier=cashier, day=day, expected=expected, counted=counted, difference=difference, explanation=explanation
    )


def day_reconciliation(day):
    """Totals for a day worked out two ways. Every `ok` must be true for the day to balance."""
    pay = Payment.objects.filter(created_at__date=day)
    by_method = {m.value: pay.filter(method=m).aggregate(t=Sum("amount"))["t"] or ZERO for m in Payment.Method}
    received = pay.filter(amount__gt=0).aggregate(t=Sum("amount"))["t"] or ZERO
    refunded = pay.filter(amount__lt=0).aggregate(t=Sum("amount"))["t"] or ZERO
    cash_ups = CashUp.objects.filter(day=day)
    cashiers_with_payments = set(pay.exclude(method=Payment.Method.INSURER).values_list("received_by", flat=True))
    closed = set(cash_ups.values_list("cashier", flat=True))
    invoices = Invoice.objects.filter(finalised_at__date=day).exclude(status=Invoice.Status.VOID)
    billed = sum((i.total for i in invoices), ZERO)
    return {
        "day": day,
        "invoices": invoices.count(),
        "billed": billed,
        "by_method": by_method,
        "received": received,
        "refunded": refunded,
        "net": received + refunded,
        "ledger_ok": sum(by_method.values(), ZERO) == received + refunded,
        "unclosed_cashiers": sorted(cashiers_with_payments - closed - {None}),
        "cash_ups": cash_ups,
        "unexplained": [c for c in cash_ups if c.difference != 0 and not c.explanation],
        "difference_total": sum((c.difference for c in cash_ups), ZERO),
    }


# ---------------------------------------------------------------- claims


SHA_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-]{3,29}$")


def validate_member_number(payer_kind, member_number):
    """SHA rejects claims without the member's number, so refuse to make or resubmit one without it."""
    if payer_kind != PayerKind.SHA:
        return
    number = (member_number or "").strip()
    if not number:
        raise ValidationError("A SHA claim needs the patient's SHA number. Add it to the patient's record first.")
    if not SHA_NUMBER.match(number):
        raise ValidationError(
            "The SHA number looks wrong. Use 4 to 30 letters, digits, dashes or slashes, with no spaces."
        )


def create_claim(invoice, user, patient_portion=0):
    """Claim the invoice from the payer. `patient_portion` is the co-pay the patient pays at the till instead."""
    patient_portion = _money(patient_portion or 0)
    if invoice.payer_kind not in CLAIM_PAYERS:
        raise ValidationError("Only invoices for SHA, insurers or corporate payers have claims.")
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
        raise ValidationError("Finalise the invoice before making a claim.")
    if hasattr(invoice, "claim"):
        raise ValidationError("This invoice already has a claim.")
    validate_member_number(invoice.payer_kind, invoice.member_number)
    if patient_portion < 0 or patient_portion >= invoice.total:
        raise ValidationError("The patient's co-pay must be between 0 and less than the invoice total.")
    diagnoses = ""
    if invoice.appointment_id:
        consultation = getattr(invoice.appointment, "consultation", None)
        if consultation:
            diagnoses = "; ".join(f"{d.icd10_code} {d.name}".strip() for d in consultation.diagnoses.all())[:300]
    visit_date = invoice.appointment.visit_date if invoice.appointment_id else timezone.localdate(invoice.finalised_at)
    with transaction.atomic():
        claim = Claim.objects.create(
            invoice=invoice,
            payer_kind=invoice.payer_kind,
            payer_name=invoice.payer_name,
            member_number=invoice.member_number,
            diagnoses=diagnoses,
            batch=f"{visit_date:%Y-%m}",
            amount_claimed=invoice.total - patient_portion,
            patient_portion=patient_portion,
        )
        for line in invoice.active_lines:
            ClaimLine.objects.create(
                claim=claim,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                amount=line.amount,
            )
    return claim


def _claim_move(claim, allowed_from, to):
    if claim.status not in allowed_from:
        raise ValidationError(f"A {claim.get_status_display().lower()} claim cannot become {to}.")
    claim.status = to


def reject_claim(claim, reason):
    if not reason.strip():
        raise ValidationError("Write the payer's reason for rejecting the claim.")
    _claim_move(claim, ("submitted", "approved"), Claim.Status.REJECTED)
    claim.rejection_reason = reason.strip()
    claim.save()


def correct_claim(claim, member_number, diagnoses):
    """Fix the details of a rejected claim and return it to draft, ready to export again."""
    _claim_move(claim, ("rejected",), Claim.Status.DRAFT)
    validate_member_number(claim.payer_kind, member_number)
    claim.member_number = member_number.strip()
    claim.diagnoses = diagnoses
    claim.resubmissions += 1
    claim.save()


def approve_claim(claim, amount):
    _claim_move(claim, ("submitted",), Claim.Status.APPROVED)
    claim.amount_approved = _money(amount)
    claim.rejection_reason = ""
    claim.save()


def mark_claim_paid(claim, user, amount, reference=""):
    """The payer has paid. Record it as an insurer payment on the invoice so the invoice balance falls."""
    _claim_move(claim, ("approved", "submitted"), Claim.Status.PAID)
    with transaction.atomic():
        take_payment(claim.invoice, Payment.Method.INSURER, amount, user, reference=reference or claim.number)
        claim.paid_at = timezone.now()
        claim.save()


OPEN_CLAIM_STATUSES = (Claim.Status.SUBMITTED, Claim.Status.APPROVED)


def claim_overdue_days():
    from django.conf import settings

    return getattr(settings, "CLAIM_OVERDUE_DAYS", 30)


def claim_ageing(payer_kind=None, today=None):
    """Claims sent to a payer and not yet paid, oldest first, with the days they have been outstanding."""
    today = today or timezone.localdate()
    limit = claim_overdue_days()
    claims = Claim.objects.filter(status__in=OPEN_CLAIM_STATUSES).select_related("invoice__patient")
    if payer_kind:
        claims = claims.filter(payer_kind=payer_kind)
    rows = []
    for claim in claims:
        sent = timezone.localtime(claim.submitted_at).date() if claim.submitted_at else today
        days = (today - sent).days
        last = timezone.localtime(claim.last_followed_up_at).date() if claim.last_followed_up_at else None
        rows.append(
            {
                "claim": claim,
                "days": days,
                "overdue": days > limit,
                "since_follow_up": (today - last).days if last else None,
            }
        )
    rows.sort(key=lambda r: (-r["days"], r["claim"].pk))
    return rows


def follow_up_claims(claims, user=None):
    """Note that the payer was chased today. Only claims that are still waiting on the payer are stamped."""
    now = timezone.now()
    done = 0
    for claim in claims:
        if claim.status in OPEN_CLAIM_STATUSES:
            claim.last_followed_up_at = now
            claim.save(update_fields=["last_followed_up_at", "updated_at"])
            done += 1
    return done


def insurer_reconciliation(batch):
    """For one month of visits, per payer: what was claimed, approved, paid, rejected, still outstanding and written off."""
    labels = dict(PayerKind.choices)
    groups = {}
    claims = Claim.objects.filter(batch=batch).select_related("invoice").prefetch_related("invoice__payments")
    for claim in claims:
        key = (claim.payer_kind, claim.payer_name)
        g = groups.setdefault(
            key,
            {
                "payer": f"{labels.get(claim.payer_kind, claim.payer_kind)} {claim.payer_name}".strip(),
                "claims": 0,
                "claimed": ZERO,
                "approved": ZERO,
                "paid": ZERO,
                "not_sent": ZERO,
                "outstanding": ZERO,
                "rejected": ZERO,
                "write_off": ZERO,
                "patient_portion": ZERO,
            },
        )
        paid = sum((p.amount for p in claim.invoice.payments.all() if p.method == Payment.Method.INSURER), ZERO)
        g["claims"] += 1
        g["claimed"] += claim.amount_claimed
        g["patient_portion"] += claim.patient_portion
        g["paid"] += paid
        if claim.status != Claim.Status.REJECTED and claim.amount_approved is not None:
            g["approved"] += claim.amount_approved
        if claim.status == Claim.Status.DRAFT:
            g["not_sent"] += claim.amount_claimed
        elif claim.status == Claim.Status.REJECTED:
            g["rejected"] += claim.amount_claimed
        elif claim.status == Claim.Status.PAID:
            g["write_off"] += max(claim.amount_claimed - paid, ZERO)
        else:
            g["outstanding"] += claim.amount_claimed - paid
    rows = sorted(groups.values(), key=lambda g: g["payer"])
    keys = ("claims", "claimed", "approved", "paid", "not_sent", "outstanding", "rejected", "write_off", "patient_portion")
    totals = {k: sum((r[k] for r in rows), 0 if k == "claims" else ZERO) for k in keys}
    return {"batch": batch, "rows": rows, "totals": totals}


CLAIM_COLUMNS = [
    "claim_number", "payer", "payer_name", "member_number", "hospital_number", "patient", "visit_date",
    "diagnoses", "invoice_number", "line_description", "quantity", "unit_price", "line_amount", "claim_total",
    "resubmission", "patient_portion", "facility_code",
]  # fmt: skip


def export_claims(claims, mark_submitted=True):
    """One CSV for the given claims, a row per claim line. Drafts become submitted."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CLAIM_COLUMNS)
    now = timezone.now()
    from django.conf import settings

    facility_code = getattr(settings, "HOSPITAL_FACILITY_CODE", "")
    for claim in claims.select_related("invoice__patient", "invoice__appointment").prefetch_related("lines"):
        patient = claim.invoice.patient
        visit = claim.invoice.appointment.visit_date if claim.invoice.appointment_id else ""
        for line in claim.lines.all():
            writer.writerow(
                [
                    claim.number, claim.get_payer_kind_display(), claim.payer_name, claim.member_number,
                    patient.hospital_number, patient.full_name, visit, claim.diagnoses, claim.invoice.number,
                    line.description, line.quantity, line.unit_price, line.amount, claim.amount_claimed,
                    claim.resubmissions, claim.patient_portion, facility_code,
                ]
            )  # fmt: skip
        if mark_submitted and claim.status == Claim.Status.DRAFT:
            claim.status = Claim.Status.SUBMITTED
            claim.submitted_at = now
            claim.save()
    return out.getvalue()


# ---------------------------------------------------------------- patient clearance

def clearance_status(appointment):
    """Check whether a visit is financially clear to discharge.

    Returns (cleared: bool, reason: str).

    Rules:
    - At least one non-void invoice must exist.
    - No invoice may still be a draft (must be finalised).
    - Cash / M-Pesa / Card payers: balance must be zero.
    - SHA / Insurer / Corporate payers: a claim must exist and must not be
      in REJECTED status (submitted, approved, or paid all pass).
    - No unresolved ChargeAlerts may remain on the visit.
    """
    from .models import ChargeAlert, Claim

    invoices = list(
        Invoice.objects.filter(appointment=appointment).exclude(status=Invoice.Status.VOID)
    )

    if not invoices:
        return False, "No invoice has been raised for this visit."

    for inv in invoices:
        if inv.status == Invoice.Status.DRAFT:
            label = inv.number if inv.number else f"draft (id {inv.pk})"
            return False, f"Invoice {label} has not been finalised yet."

        if inv.payer_kind in CLAIM_PAYERS:
            try:
                claim = inv.claim
            except Claim.DoesNotExist:
                claim = None
            if claim is None:
                return False, (
                    f"Invoice {inv.number}: no claim has been created for this "
                    f"{inv.get_payer_kind_display()} patient. Create the claim before discharging."
                )
            if claim.status == Claim.Status.REJECTED:
                return False, (
                    f"Claim {claim.number} was rejected "
                    f"({claim.rejection_reason or 'no reason recorded'}). "
                    f"Correct and resubmit it, or collect from the patient directly."
                )
            if claim.patient_portion > ZERO:
                from_patient = (
                    inv.payments.exclude(method=Payment.Method.INSURER).aggregate(t=Sum("amount"))["t"] or ZERO
                )
                if from_patient < claim.patient_portion:
                    return False, (
                        f"Invoice {inv.number}: the patient's co-pay, KES {claim.patient_portion - from_patient}, "
                        f"has not been collected."
                    )
        else:
            # Cash / M-Pesa / Card: full payment expected before discharge.
            if inv.balance > ZERO:
                return False, (
                    f"Invoice {inv.number}: KES {inv.balance} is still outstanding. "
                    f"Collect payment or create a claim before discharging."
                )

    unresolved_alerts = ChargeAlert.objects.filter(
        appointment=appointment, resolved_at__isnull=True
    ).exists()
    if unresolved_alerts:
        return False, (
            "There are unresolved charge alerts on this visit. "
            "Open the invoice, resolve the alerts, then discharge."
        )

    return True, "All invoices settled or claimed."


# ---------------------------------------------------------------- monthly report


def _month_bounds(year, month):
    start = timezone.make_aware(datetime.datetime(year, month, 1))
    end = timezone.make_aware(datetime.datetime(year + (month == 12), month % 12 + 1, 1))
    return start, end


def monthly_report(year, month):
    """What was billed, collected and is still owed for one month.

    Billed: invoices finalised in the month (void ones left out), by payer and by kind of service.
    Collected: money received or refunded in the month, by method (it may belong to earlier invoices).
    Outstanding: what is still unpaid today on the invoices finalised in the month.
    """
    start, end = _month_bounds(year, month)
    invoices = Invoice.objects.filter(finalised_at__gte=start, finalised_at__lt=end).exclude(
        status__in=(Invoice.Status.DRAFT, Invoice.Status.VOID)
    )
    lines = InvoiceLine.objects.filter(invoice__in=invoices, removed_at__isnull=True)
    payer_labels = dict(PayerKind.choices)
    category_labels = dict(ServiceItem.Category.choices)
    method_labels = dict(Payment.Method.choices)

    billed = {r["invoice__payer_kind"]: r["t"] for r in lines.values("invoice__payer_kind").annotate(t=Sum("amount"))}
    paid = {
        r["invoice__payer_kind"]: r["t"]
        for r in Payment.objects.filter(invoice__in=invoices).values("invoice__payer_kind").annotate(t=Sum("amount"))
    }
    counts = {r["payer_kind"]: r["n"] for r in invoices.values("payer_kind").annotate(n=Count("id"))}
    by_payer = []
    for kind in sorted(billed, key=lambda k: payer_labels.get(k, k)):
        b, p = billed[kind], paid.get(kind) or ZERO
        by_payer.append(
            {"payer": payer_labels.get(kind, kind), "invoices": counts.get(kind, 0), "billed": b, "paid": p, "outstanding": b - p}
        )
    by_category = [
        {"category": category_labels.get(r["service__category"], r["service__category"]), "billed": r["t"]}
        for r in lines.values("service__category").annotate(t=Sum("amount")).order_by("-t")
    ]
    payments = Payment.objects.filter(created_at__gte=start, created_at__lt=end)
    collected = [
        {"method": method_labels.get(r["method"], r["method"]), "amount": r["t"]}
        for r in payments.values("method").annotate(t=Sum("amount")).order_by("method")
    ]
    claim_rows = [
        {"status": Claim.Status(r["status"]).label, "claims": r["n"], "claimed": r["t"]}
        for r in Claim.objects.filter(batch=f"{year}-{month:02d}")
        .values("status")
        .annotate(n=Count("id"), t=Sum("amount_claimed"))
        .order_by("status")
    ]
    total_billed = sum((r["billed"] for r in by_payer), ZERO)
    total_paid = sum((r["paid"] for r in by_payer), ZERO)
    return {
        "year": year,
        "month": month,
        "label": f"{year}-{month:02d}",
        "by_payer": by_payer,
        "by_category": by_category,
        "collected": collected,
        "claims": claim_rows,
        "totals": {
            "invoices": sum((r["invoices"] for r in by_payer), 0),
            "billed": total_billed,
            "paid": total_paid,
            "outstanding": total_billed - total_paid,
            "collected": sum((r["amount"] for r in collected), ZERO),
        },
    }


def monthly_report_csv(report):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Monthly revenue report", report["label"]])
    w.writerow([])
    w.writerow(["Billed by payer"])
    w.writerow(["payer", "invoices", "billed", "paid_to_date", "outstanding"])
    for r in report["by_payer"]:
        w.writerow([r["payer"], r["invoices"], r["billed"], r["paid"], r["outstanding"]])
    t = report["totals"]
    w.writerow(["Total", t["invoices"], t["billed"], t["paid"], t["outstanding"]])
    w.writerow([])
    w.writerow(["Billed by kind of service"])
    w.writerow(["category", "billed"])
    for r in report["by_category"]:
        w.writerow([r["category"], r["billed"]])
    w.writerow([])
    w.writerow(["Collected in the month, by method"])
    w.writerow(["method", "amount"])
    for r in report["collected"]:
        w.writerow([r["method"], r["amount"]])
    w.writerow(["Total", t["collected"]])
    w.writerow([])
    w.writerow(["Claims for visits in the month"])
    w.writerow(["status", "claims", "claimed"])
    for r in report["claims"]:
        w.writerow([r["status"], r["claims"], r["claimed"]])
    return out.getvalue()
