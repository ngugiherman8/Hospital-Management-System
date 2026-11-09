"""All the billing rules live here so views, signals and tests use the same code."""

import json
import logging
import re
import urllib.request
from base64 import b64encode
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Invoice, InvoiceLine, MpesaRequest, Payer, Payment, ServiceItem

log = logging.getLogger(__name__)
CENT = Decimal("0.01")


class BillingError(Exception):
    """A billing step that is not allowed. The message is safe to show to staff."""


# ------------------------------------------------------------------------------------------------ services
BASE_SERVICES = {
    "REG": ("Registration fee", ServiceItem.Category.REGISTRATION),
    "CONSULT": ("Consultation", ServiceItem.Category.CONSULTATION),
    "BED-GENERAL": ("Bed day, general ward", ServiceItem.Category.BED_DAY),
    "BED-MATERNITY": ("Bed day, maternity", ServiceItem.Category.BED_DAY),
    "BED-PAEDIATRIC": ("Bed day, paediatric", ServiceItem.Category.BED_DAY),
    "BED-PRIVATE": ("Bed day, private ward", ServiceItem.Category.BED_DAY),
}


def base_service(code):
    name, category = BASE_SERVICES[code]
    return ServiceItem.objects.get_or_create(code=code, defaults={"name": name, "category": category})[0]


def service_for_test(test):
    category = {"radiology": "imaging", "procedure": "procedure"}.get(test.kind, "lab")
    return ServiceItem.objects.get_or_create(
        test=test, defaults={"code": f"TEST-{test.pk}", "name": test.name, "category": category}
    )[0]


def service_for_drug(drug):
    return ServiceItem.objects.get_or_create(
        drug=drug, defaults={"code": f"DRUG-{drug.pk}", "name": drug.label, "category": "drug"}
    )[0]


def sync_service_items():
    """Make sure every test and drug in the catalogues has a billable item to put a price on."""
    from apps.orders.models import TestCatalogue
    from apps.pharmacy_rx.models import Drug

    for code in BASE_SERVICES:
        base_service(code)
    for t in TestCatalogue.objects.all():
        service_for_test(t)
    for d in Drug.objects.all():
        service_for_drug(d)
    return ServiceItem.objects.count()


# ------------------------------------------------------------------------------------------------ charges
def target_invoice(visit):
    """The visit's draft invoice, or a new one. Charges arriving after finalising go on a new draft."""
    inv = visit.invoices.filter(status=Invoice.Status.DRAFT).order_by("-id").first()
    if inv:
        return inv
    cover = visit.patient.covers.first()
    return Invoice.objects.create(patient=visit.patient, visit=visit, payer=Payer.for_cover(cover))


def add_charge(visit, service, source_key, quantity=1, description=None, user=None):
    """Add one charge for one clinical event. Does nothing if that event was already charged."""
    if source_key and InvoiceLine.objects.filter(source_key=source_key).exists():
        return None
    inv = target_invoice(visit)
    price = service.price_for(inv.payer.payer_type)
    return InvoiceLine.objects.create(
        invoice=inv,
        service=service,
        description=description or service.name,
        quantity=quantity,
        unit_price=price if price is not None else Decimal("0"),
        price_missing=price is None,
        source_key=source_key,
        created_by=user,
    )


def bed_days(admission):
    """One bed day for each calendar day in ward. Admitted today counts as day 1."""
    start = timezone.localtime(admission.admitted_at).date()
    if admission.discharged_at:
        return max((timezone.localtime(admission.discharged_at).date() - start).days, 1)
    return (timezone.localdate() - start).days + 1


def sync_charges(visit):
    """Turn what the clinical team recorded into draft charges. Safe to run any number of times."""
    from apps.wards.models import Admission

    patient = visit.patient
    add_charge(visit, base_service("REG"), f"reg:{patient.pk}", description="Registration fee")
    if visit.finished_at:
        add_charge(visit, base_service("CONSULT"), f"consult:{visit.pk}", description="Consultation")
    for order in visit.orders.select_related("test"):
        key = f"order:{order.pk}"
        if order.status == "cancelled":
            line = InvoiceLine.objects.filter(source_key=key, is_removed=False, invoice__status="draft").first()
            if line:
                remove_line(line, None, "Test cancelled")
            continue
        add_charge(visit, service_for_test(order.test), key, description=f"{order.test.name} ({order.number})")
    for rx in visit.prescriptions.filter(status="dispensed").select_related("drug"):
        add_charge(
            visit,
            service_for_drug(rx.drug),
            f"rx:{rx.pk}",
            quantity=rx.quantity,
            description=f"{rx.drug.label} x {rx.quantity.normalize():f}",
        )
    adm = Admission.objects.filter(visit=visit).select_related("bed__ward").first()
    if adm:
        code = f"BED-{adm.bed.ward.ward_type.upper()}"
        code = code if code in BASE_SERVICES else "BED-GENERAL"
        for n in range(1, bed_days(adm) + 1):
            add_charge(visit, base_service(code), f"bed:{adm.pk}:{n}", description=f"Bed day {n}, {adm.bed.ward.name}")
    reprice_draft_lines(visit)


def reprice_draft_lines(visit):
    """A price set after the charge arrived is picked up while the invoice is still a draft."""
    lines = InvoiceLine.objects.filter(
        invoice__visit=visit, invoice__status="draft", price_missing=True, is_removed=False
    )
    for line in lines.select_related("service", "invoice__payer"):
        price = line.service.price_for(line.invoice.payer.payer_type) if line.service else None
        if price is not None:
            line.unit_price, line.price_missing = price, False
            line.save()


def remove_line(line, user, reason=""):
    line.is_removed, line.removed_by, line.removed_at = True, user, timezone.now()
    line.reason = reason[:200] or line.reason
    line.save()


# ------------------------------------------------------------------------------------------------ invoice steps
def add_manual_line(invoice, service, quantity, user):
    if not invoice.is_draft:
        raise BillingError("Lines can only be added while the invoice is a draft.")
    if quantity <= 0:
        raise BillingError("Quantity must be more than zero.")
    price = service.price_for(invoice.payer.payer_type)
    return InvoiceLine.objects.create(
        invoice=invoice,
        service=service,
        description=service.name,
        quantity=quantity,
        unit_price=price if price is not None else Decimal("0"),
        price_missing=price is None,
        created_by=user,
    )


def remove_manual_line(line, user, reason=""):
    if not line.invoice.is_draft:
        raise BillingError(
            "Lines can only be removed while the invoice is a draft. Ask an administrator for a credit note."
        )
    remove_line(line, user, reason)


def finalise(invoice, user):
    if not invoice.is_draft:
        raise BillingError("Only a draft invoice can be finalised.")
    if invoice.visit_id:
        reprice_draft_lines(invoice.visit)
    if not invoice.active_lines.exists():
        raise BillingError("The invoice has no lines.")
    missing = list(invoice.active_lines.filter(price_missing=True).values_list("description", flat=True))
    if missing:
        raise BillingError(f"No price is set for: {', '.join(missing)}. Ask an administrator to set the price.")
    invoice.status = Invoice.Status.OPEN
    invoice.finalised_at, invoice.finalised_by = timezone.now(), user
    invoice.save()
    invoice.refresh_status()
    return invoice


def add_credit_note(invoice, amount, reason, user):
    if invoice.is_draft or invoice.is_void:
        raise BillingError("Credit notes are for finalised invoices. Remove the line on a draft instead.")
    amount = Decimal(amount).quantize(CENT)
    if amount <= 0:
        raise BillingError("Enter the credit as a positive amount.")
    if not reason.strip():
        raise BillingError("A reason is required.")
    if amount > invoice.total:
        raise BillingError("A credit cannot be larger than the invoice total.")
    InvoiceLine.objects.create(
        invoice=invoice,
        description=f"Credit note: {reason.strip()}"[:200],
        quantity=1,
        unit_price=-amount,
        kind=InvoiceLine.Kind.ADJUSTMENT,
        reason=reason.strip()[:200],
        created_by=user,
    )
    invoice.refresh_status()


def void_invoice(invoice, reason, user):
    if invoice.is_void:
        raise BillingError("This invoice is already void.")
    if not reason.strip():
        raise BillingError("A reason is required to void an invoice.")
    if invoice.paid != 0:
        raise BillingError("Refund the payments on this invoice first, then void it.")
    invoice.status, invoice.void_reason = Invoice.Status.VOID, reason.strip()[:200]
    invoice.voided_by, invoice.voided_at = user, timezone.now()
    invoice.save()


MPESA_CODE = re.compile(r"^[A-Z0-9]{8,12}$")


def take_payment(invoice, method, amount, user, *, mpesa_code="", reference="", is_deposit=False, external_id=""):
    amount = Decimal(amount).quantize(CENT)
    if invoice.is_void:
        raise BillingError("This invoice is void.")
    if amount <= 0:
        raise BillingError("Enter an amount more than zero.")
    if invoice.is_draft and not is_deposit:
        raise BillingError("Finalise the invoice before taking payment. Use 'deposit' to take money in advance.")
    if not is_deposit and amount > invoice.balance:
        raise BillingError(f"The balance is {invoice.balance:,.2f}. Take less, or record the extra as a deposit.")
    code = (mpesa_code or "").strip().upper()
    if method == Payment.Method.MPESA:
        if not external_id and not MPESA_CODE.match(code):
            raise BillingError("Enter the M-Pesa transaction code (8 to 12 letters and numbers).")
    else:
        code = ""
    try:
        with transaction.atomic():
            pay = Payment.objects.create(
                invoice=invoice, method=method, amount=amount, is_deposit=is_deposit, mpesa_code=code,
                external_id=external_id, reference=reference.strip()[:120], received_by=user,
            )  # fmt: skip
    except IntegrityError:
        raise BillingError("That M-Pesa code or transaction has already been recorded.") from None
    invoice.refresh_status()
    return pay


def refund(invoice, amount, method, reason, user):
    amount = Decimal(amount).quantize(CENT)
    if not reason.strip():
        raise BillingError("A reason is required for a refund.")
    if amount <= 0:
        raise BillingError("Enter the refund as a positive amount.")
    if amount > invoice.paid:
        raise BillingError(f"Only {invoice.paid:,.2f} has been paid on this invoice.")
    pay = Payment.objects.create(
        invoice=invoice, method=method, amount=-amount, refund_reason=reason.strip()[:200], received_by=user,
        approved_by=user,
    )  # fmt: skip
    invoice.refresh_status()
    return pay


# ------------------------------------------------------------------------------------------------ M-Pesa
def _post(url, payload, headers):
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        return json.loads(resp.read())


def _get_token():
    base = "https://api.safaricom.co.ke" if settings.MPESA_ENV == "production" else "https://sandbox.safaricom.co.ke"
    cred = b64encode(f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}".encode()).decode()
    req = urllib.request.Request(
        f"{base}/oauth/v1/generate?grant_type=client_credentials", headers={"Authorization": f"Basic {cred}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        return base, json.loads(resp.read())["access_token"]


def mpesa_configured():
    s = settings
    return all([s.MPESA_CONSUMER_KEY, s.MPESA_CONSUMER_SECRET, s.MPESA_SHORTCODE, s.MPESA_PASSKEY,
                s.MPESA_CALLBACK_TOKEN, s.MPESA_CALLBACK_BASE])  # fmt: skip


def send_mpesa_prompt(invoice, phone, amount, user):
    """Ask the patient's phone for payment. The result arrives later, at the callback."""
    if not mpesa_configured():
        raise BillingError("Automatic M-Pesa is not set up yet. Type in the M-Pesa code instead.")
    amount = Decimal(amount).quantize(CENT)
    if invoice.is_draft or invoice.is_void or amount <= 0 or amount > invoice.balance:
        raise BillingError("Finalise the invoice and ask for no more than the balance.")
    from apps.patients.models import normalize_phone

    msisdn = normalize_phone(phone)
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    password = b64encode(f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{stamp}".encode()).decode()
    base, token = _get_token()
    body = _post(
        f"{base}/mpesa/stkpush/v1/processrequest",
        {
            "BusinessShortCode": settings.MPESA_SHORTCODE, "Password": password, "Timestamp": stamp,
            "TransactionType": "CustomerPayBillOnline", "Amount": int(amount), "PartyA": msisdn,
            "PartyB": settings.MPESA_SHORTCODE, "PhoneNumber": msisdn,
            "CallBackURL": f"{settings.MPESA_CALLBACK_BASE}/billing/mpesa/callback/{settings.MPESA_CALLBACK_TOKEN}/",
            "AccountReference": invoice.number, "TransactionDesc": f"Hospital bill {invoice.number}",
        },
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )  # fmt: skip
    cid = body.get("CheckoutRequestID")
    if not cid:
        raise BillingError(f"M-Pesa did not accept the request: {body.get('errorMessage') or body}")
    return MpesaRequest.objects.create(
        checkout_request_id=cid, invoice=invoice, amount=amount, phone=msisdn, created_by=user
    )


def apply_mpesa_callback(payload):
    """Handle Safaricom's result. Each provider transaction becomes one payment, however often it is sent."""
    cb = (payload.get("Body") or {}).get("stkCallback") or {}
    cid = cb.get("CheckoutRequestID", "")
    req = MpesaRequest.objects.filter(checkout_request_id=cid).select_related("invoice").first()
    if not req:
        log.warning("M-Pesa callback for an unknown request: %s", cid)
        return None
    if cb.get("ResultCode") != 0:
        if req.status == MpesaRequest.Status.PENDING:
            req.status, req.result_desc = MpesaRequest.Status.FAILED, str(cb.get("ResultDesc", ""))[:200]
            req.save()
        return None
    items = {i.get("Name"): i.get("Value") for i in (cb.get("CallbackMetadata") or {}).get("Item", [])}
    amount = Decimal(str(items.get("Amount", req.amount)))
    receipt = str(items.get("MpesaReceiptNumber", "")).upper()
    with transaction.atomic():
        existing = Payment.objects.filter(external_id=cid).first()
        if existing:
            return existing  # a repeat of a callback we already handled
        try:
            with transaction.atomic():
                pay = Payment.objects.create(
                    invoice=req.invoice, method=Payment.Method.MPESA, amount=amount, mpesa_code=receipt,
                    external_id=cid, reference=f"STK {items.get('PhoneNumber', req.phone)}", received_by=req.created_by,
                )  # fmt: skip
        except IntegrityError:
            return Payment.objects.filter(external_id=cid).first()  # two copies arrived at the same moment
        req.status = MpesaRequest.Status.PAID
        req.save()
        req.invoice.refresh_status()
        return pay
