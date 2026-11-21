import hmac
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core.audit import log_access
from apps.core.permissions import role_required

from . import services
from .forms import (
    AddLineForm,
    CashUpForm,
    ClaimAmountForm,
    ClaimCorrectForm,
    ClaimFilterForm,
    PaymentForm,
    ReasonForm,
    RefundForm,
)
from .models import CashUp, ChargeAlert, Claim, Invoice, InvoiceLine, ServiceItem

CASHIER = (Role.CASHIER,)  # administrators always pass
ADMIN = ()  # no listed role: administrators only


def _errors(request, exc):
    messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


@role_required(*CASHIER)
def invoice_list(request):
    qs = Invoice.objects.select_related("patient")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(
            Q(number__icontains=q) | Q(patient__hospital_number__icontains=q) | Q(patient__last_name__icontains=q)
            | Q(patient__first_name__icontains=q)
        )  # fmt: skip
    if status in Invoice.Status.values:
        qs = qs.filter(status=status)
    else:
        status = ""
    return render(
        request,
        "billing/invoice_list.html",
        {"invoices": qs[:100], "q": q, "status": status, "statuses": Invoice.Status.choices},
    )


@role_required(*CASHIER)
def visit_invoices(request, pk):
    """Open the invoice for a visit. Goes straight to it when there is only one."""
    from apps.appointments.models import Appointment

    visit = get_object_or_404(Appointment, pk=pk)
    invoices = list(visit.invoices.all())
    if not invoices:
        invoice = services.draft_invoice_for(visit.patient, visit, request.user)
        return redirect("invoice_detail", pk=invoice.pk)
    if len(invoices) == 1:
        return redirect("invoice_detail", pk=invoices[0].pk)
    return render(request, "billing/visit_invoices.html", {"visit": visit, "invoices": invoices})


def _invoice_context(invoice, request, **extra):
    ctx = {
        "invoice": invoice,
        "lines": invoice.active_lines.select_related("service"),
        "removed": invoice.lines.filter(removed_at__isnull=False),
        "payments": invoice.payments.all(),
        "add_form": AddLineForm(),
        "pay_form": PaymentForm(initial={"amount": invoice.balance}),
        "void_form": ReasonForm(),
        "refund_form": RefundForm(),
        "alerts": ChargeAlert.objects.filter(
            resolved_at__isnull=True, patient=invoice.patient, appointment=invoice.appointment
        ),
        "can_manage": request.user.has_role(Role.ADMIN),
    }
    ctx.update(extra)
    return ctx


@role_required(*CASHIER)
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("patient", "appointment"), pk=pk)
    log_access(invoice)
    return render(request, "billing/invoice_detail.html", _invoice_context(invoice, request))


@role_required(*CASHIER)
def invoice_print(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("patient", "appointment"), pk=pk)
    log_access(invoice, "printed")
    return render(request, "billing/invoice_print.html", _invoice_context(invoice, request))


@role_required(*CASHIER)
def receipt(request, pk):
    """Printable receipt for a single payment (or refund)."""
    from .models import Payment

    payment = get_object_or_404(
        Payment.objects.select_related("invoice__patient", "received_by"),
        pk=pk,
    )
    log_access(payment)
    return render(
        request,
        "billing/receipt.html",
        {
            "pay": payment,
            "invoice": payment.invoice,
            "patient": payment.invoice.patient,
            "lines": payment.invoice.active_lines.select_related("service"),
        },
    )


def _invoice_post(view):
    """Shared shape of the POST actions: load the invoice, run the action, report, go back."""

    @role_required(*CASHIER)
    @require_POST
    def wrapper(request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        try:
            view(request, invoice)
        except (ValidationError, PermissionError) as exc:
            _errors(request, exc)
        return redirect("invoice_detail", pk=invoice.pk)

    wrapper.__name__ = view.__name__
    return wrapper


@_invoice_post
def add_line(request, invoice):
    form = AddLineForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Choose a service and a quantity.")
    services.add_line(invoice, form.cleaned_data["service"], form.cleaned_data["quantity"], request.user)
    messages.success(request, "Line added.")


@_invoice_post
def remove_line(request, invoice):
    line = get_object_or_404(InvoiceLine, pk=request.POST.get("line"), invoice=invoice)
    services.remove_line(line, request.user, request.POST.get("reason", ""))
    messages.success(request, "Line removed.")


@_invoice_post
def finalise(request, invoice):
    invoice = services.finalise(invoice, request.user)
    messages.success(request, f"Invoice {invoice.number} is final.")


@_invoice_post
def pay(request, invoice):
    form = PaymentForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Check the payment details.")
    d = form.cleaned_data
    payment = services.take_payment(
        invoice, d["method"], d["amount"], request.user, mpesa_code=d["mpesa_code"], reference=d["reference"]
    )
    messages.success(request, f"Receipt {payment.receipt_number} issued.")


@_invoice_post
def void(request, invoice):
    form = ReasonForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Give a reason for voiding the invoice.")
    services.void_invoice(invoice, request.user, form.cleaned_data["reason"])
    messages.success(request, "Invoice voided.")


@_invoice_post
def refund(request, invoice):
    form = RefundForm(request.POST)
    if not form.is_valid():
        raise ValidationError("Give an amount and a reason.")
    d = form.cleaned_data
    payment = services.refund(invoice, d["amount"], request.user, d["reason"], d["method"])
    messages.success(request, f"Refund {payment.receipt_number} recorded.")


@_invoice_post
def create_claim(request, invoice):
    claim = services.create_claim(invoice, request.user)
    messages.success(request, f"Claim {claim.number} created.")


@role_required(*CASHIER)
@require_POST
def alert_resolve(request, pk):
    alert = get_object_or_404(ChargeAlert, pk=pk)
    alert.resolved_at = timezone.now()
    alert.resolved_by = request.user
    alert.save()
    invoice = Invoice.objects.filter(patient=alert.patient, appointment=alert.appointment).first()
    return redirect("invoice_detail", pk=invoice.pk) if invoice else redirect("invoice_list")


@role_required(*CASHIER)
def price_list(request):
    items = ServiceItem.objects.filter(is_active=True).prefetch_related("prices")
    return render(request, "billing/price_list.html", {"items": items})


# ------------------------------------------------------------ cash-up


@role_required(*CASHIER)
def cash_up(request):
    today = timezone.localdate()
    is_admin = request.user.has_role(Role.ADMIN)
    mine = CashUp.objects.filter(cashier=request.user, day=today).first()
    form = CashUpForm(request.POST or None)
    if request.method == "POST" and mine is None:
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.close_day(
                    request.user, today, {k: d[k] for k in ("cash", "mpesa", "card", "deposit")}, d["explanation"]
                )
            except ValidationError as exc:
                _errors(request, exc)
            else:
                messages.success(request, "Your day is closed.")
                return redirect("cash_up")
    history = CashUp.objects.select_related("cashier")
    if not is_admin:
        history = history.filter(cashier=request.user)
    return render(
        request,
        "billing/cash_up.html",
        {
            "form": form,
            "mine": mine,
            "expected": services.expected_for(request.user, today),
            "history": history[:60],
            "is_admin": is_admin,
            "today": today,
        },
    )


@role_required(*ADMIN)
def day_report(request):
    try:
        day = timezone.datetime.fromisoformat(request.GET["day"]).date() if request.GET.get("day") else None
    except ValueError:
        day = None
    day = day or timezone.localdate()
    from apps.stock.services import day_stock_reconciliation

    return render(
        request,
        "billing/day_report.html",
        {"r": services.day_reconciliation(day), "stock": day_stock_reconciliation(day), "day": day},
    )


# ------------------------------------------------------------ claims


@role_required(*CASHIER)
def claim_list(request):
    form = ClaimFilterForm(request.GET)
    qs = Claim.objects.select_related("invoice__patient")
    if form.is_valid():
        if form.cleaned_data["month"]:
            qs = qs.filter(batch=form.cleaned_data["month"])
        if form.cleaned_data["status"]:
            qs = qs.filter(status=form.cleaned_data["status"])
    return render(request, "billing/claim_list.html", {"claims": qs[:200], "form": form})


@role_required(*CASHIER)
def claim_detail(request, pk):
    claim = get_object_or_404(Claim.objects.select_related("invoice__patient"), pk=pk)
    log_access(claim)
    return render(
        request,
        "billing/claim_detail.html",
        {
            "claim": claim,
            "lines": claim.lines.all(),
            "reject_form": ReasonForm(),
            "correct_form": ClaimCorrectForm(
                initial={"member_number": claim.member_number, "diagnoses": claim.diagnoses}
            ),
            "amount_form": ClaimAmountForm(initial={"amount": claim.amount_approved or claim.amount_claimed}),
        },
    )


@role_required(*CASHIER)
@require_POST
def claim_action(request, pk, action):
    claim = get_object_or_404(Claim, pk=pk)
    try:
        if action == "reject":
            form = ReasonForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Write the rejection reason.")
            services.reject_claim(claim, form.cleaned_data["reason"])
        elif action == "correct":
            form = ClaimCorrectForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Give the member number and the diagnoses.")
            services.correct_claim(claim, form.cleaned_data["member_number"], form.cleaned_data["diagnoses"])
        elif action in ("approve", "paid"):
            form = ClaimAmountForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Give the amount.")
            if action == "approve":
                services.approve_claim(claim, form.cleaned_data["amount"])
            else:
                services.mark_claim_paid(
                    claim, request.user, form.cleaned_data["amount"], form.cleaned_data["reference"]
                )
        else:
            raise Http404
        messages.success(request, "Claim updated.")
    except (ValidationError, PermissionError) as exc:
        _errors(request, exc)
    return redirect("claim_detail", pk=claim.pk)


@role_required(*CASHIER)
def claim_export(request):
    """One file for a month of claims. Draft claims become submitted."""
    month = request.GET.get("month", "").strip()
    if len(month) != 7:
        messages.error(request, "Choose a month as YYYY-MM.")
        return redirect("claim_list")
    claims = Claim.objects.filter(batch=month, status__in=[Claim.Status.DRAFT, Claim.Status.SUBMITTED])
    if request.GET.get("payer") in ("sha", "insurer", "corporate"):
        claims = claims.filter(payer_kind=request.GET["payer"])
    log_access(Claim(number=f"export {month}", pk=0), f"exported {claims.count()} claims")
    body = services.export_claims(claims)
    response = HttpResponse(body, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="claims-{month}.csv"'
    return response


# ------------------------------------------------------------ M-Pesa


@csrf_exempt
@require_POST
def mpesa_confirmation(request):
    """Payment confirmation from M-Pesa (or a relay in front of it).

    Expects JSON {"invoice_number", "code", "amount"} and the shared secret in the X-Mpesa-Secret header.
    Sending the same code twice records one payment. It is off until MPESA_CALLBACK_SECRET is set.
    """
    secret = getattr(settings, "MPESA_CALLBACK_SECRET", "")
    if not secret:
        raise Http404
    if not hmac.compare_digest(request.headers.get("X-Mpesa-Secret", ""), secret):
        raise PermissionDenied
    try:
        data = json.loads(request.body)
        code, number, amount = str(data["code"]), str(data["invoice_number"]), Decimal(str(data["amount"]))
    except (ValueError, KeyError, InvalidOperation, TypeError):
        return JsonResponse({"ok": False, "error": "bad request"}, status=400)
    try:
        payment, created = services.record_mpesa_confirmation(number, code, amount)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=422)
    return JsonResponse({"ok": True, "receipt": payment.receipt_number, "duplicate": not created})
