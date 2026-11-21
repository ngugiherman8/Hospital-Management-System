"""M-Pesa STK Push: send a payment request to the patient's phone, then record the payment when M-Pesa confirms.

Nothing here runs until every MPESA_* setting is filled in (see .env.example). The payment itself is only ever
recorded from M-Pesa's confirmation, never from the request, so a failed or ignored prompt costs nothing.
"""

import base64
import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Invoice, MpesaPrompt
from .services import record_mpesa_confirmation

log = logging.getLogger(__name__)

HOSTS = {"sandbox": "https://sandbox.safaricom.co.ke", "production": "https://api.safaricom.co.ke"}
REQUIRED = (
    "MPESA_CONSUMER_KEY",
    "MPESA_CONSUMER_SECRET",
    "MPESA_SHORTCODE",
    "MPESA_PASSKEY",
    "MPESA_STK_CALLBACK_BASE",
    "MPESA_STK_CALLBACK_SECRET",
)
CANCELLED_BY_PATIENT = 1032  # M-Pesa's result code when the patient dismisses the prompt


def is_configured():
    return all(getattr(settings, name, "") for name in REQUIRED)


def normalize_msisdn(raw):
    """Turn 07xx, 01xx, +254... or 254... into 2547xxxxxxxx / 2541xxxxxxxx, or refuse it."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    if not (len(digits) == 12 and digits.startswith("254") and digits[3] in "71"):
        raise ValidationError("Enter a Safaricom number such as 0712 345 678.")
    return digits


def _host():
    return HOSTS.get(getattr(settings, "MPESA_ENV", "sandbox"), HOSTS["sandbox"])


def _request(path, *, body=None, headers=None):
    request = urllib.request.Request(
        _host() + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (fixed https hosts above)
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.warning("M-Pesa request to %s failed: %s", path, exc)
        raise ValidationError("Could not reach M-Pesa. Try again, or take the payment by typing the M-Pesa code.") from None


def _access_token():
    pair = f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}".encode()
    data = _request(
        "/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": "Basic " + base64.b64encode(pair).decode()},
    )
    token = data.get("access_token")
    if not token:
        raise ValidationError("M-Pesa did not accept the system's credentials. Ask the administrator to check them.")
    return token


def callback_url():
    base = settings.MPESA_STK_CALLBACK_BASE.rstrip("/")
    return f"{base}/billing/mpesa/stk/{settings.MPESA_STK_CALLBACK_SECRET}/callback/"


def send_stk(phone, amount, account_reference, description):
    """Ask M-Pesa to prompt the phone. Returns M-Pesa's JSON answer."""
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{stamp}".encode()).decode()
    return _request(
        "/mpesa/stkpush/v1/processrequest",
        body={
            "BusinessShortCode": settings.MPESA_SHORTCODE,
            "Password": password,
            "Timestamp": stamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": settings.MPESA_SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": callback_url(),
            "AccountReference": account_reference[:12],
            "TransactionDesc": description[:13],
        },
        headers={"Authorization": "Bearer " + _access_token()},
    )


def start_prompt(invoice, phone, amount, user):
    """Send a prompt for `amount` (whole shillings) on this invoice. Returns the MpesaPrompt."""
    if not is_configured():
        raise ValidationError("M-Pesa prompts are not set up on this system.")
    invoice = Invoice.objects.get(pk=invoice.pk)
    if invoice.status not in (Invoice.Status.OPEN, Invoice.Status.PART_PAID):
        raise ValidationError("Prompts can only be sent for an invoice that is waiting for payment.")
    amount = Decimal(amount)
    if amount != amount.to_integral_value() or amount < 1:
        raise ValidationError("M-Pesa prompts are for whole shillings.")
    if amount > invoice.balance:
        raise ValidationError(f"The amount is more than the balance of {invoice.balance}.")
    msisdn = normalize_msisdn(phone)
    prompt = MpesaPrompt.objects.create(invoice=invoice, phone=msisdn, amount=amount, created_by=user)
    try:
        answer = send_stk(msisdn, amount, invoice.number, "Hospital bill")
    except ValidationError as exc:
        prompt.status = MpesaPrompt.Status.FAILED
        prompt.result_desc = " ".join(exc.messages)[:200]
        prompt.save()
        raise
    if str(answer.get("ResponseCode")) != "0":
        prompt.status = MpesaPrompt.Status.FAILED
        prompt.result_desc = str(answer.get("errorMessage") or answer.get("ResponseDescription") or "Refused")[:200]
        prompt.save()
        raise ValidationError(f"M-Pesa refused the request: {prompt.result_desc}")
    prompt.checkout_request_id = str(answer.get("CheckoutRequestID", ""))[:64]
    prompt.merchant_request_id = str(answer.get("MerchantRequestID", ""))[:64]
    prompt.save()
    return prompt


def handle_stk_callback(payload):
    """Apply M-Pesa's answer to the prompt it belongs to. Safe to call twice. Returns the prompt, or None."""
    try:
        data = payload["Body"]["stkCallback"]
        checkout_id = str(data["CheckoutRequestID"])
        result_code = int(data["ResultCode"])
    except (KeyError, TypeError, ValueError):
        raise ValidationError("Not an M-Pesa confirmation.") from None
    with transaction.atomic():
        prompt = (
            MpesaPrompt.objects.select_for_update().select_related("invoice", "created_by")
            .filter(checkout_request_id=checkout_id)
            .first()
        )
        if prompt is None or prompt.status != MpesaPrompt.Status.PENDING:
            return prompt
        prompt.result_desc = str(data.get("ResultDesc", ""))[:200]
        if result_code != 0:
            prompt.status = (
                MpesaPrompt.Status.CANCELLED if result_code == CANCELLED_BY_PATIENT else MpesaPrompt.Status.FAILED
            )
            prompt.save()
            return prompt
        items = {i.get("Name"): i.get("Value") for i in (data.get("CallbackMetadata") or {}).get("Item", [])}
        code = str(items.get("MpesaReceiptNumber", "")).strip()
        try:
            amount = Decimal(str(items["Amount"]))
        except (KeyError, ArithmeticError):
            amount = prompt.amount
        if not code:
            prompt.status = MpesaPrompt.Status.FAILED
            prompt.result_desc = "M-Pesa confirmed without a receipt number. Check the M-Pesa statement."
            prompt.save()
            return prompt
        record_mpesa_confirmation(
            prompt.invoice.number, code, amount, user=prompt.created_by, reference=f"STK prompt {prompt.pk}"
        )
        prompt.status = MpesaPrompt.Status.SUCCESS
        prompt.mpesa_code = code.upper()
        prompt.save()
    return prompt
