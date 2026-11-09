import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.clinical.models import Visit
from apps.core.audit import log_access, log_event
from apps.core.permissions import RoleRequiredMixin

from . import services
from .forms import AddLineForm, CreditForm, MpesaPromptForm, PaymentForm, RefundForm, VoidForm
from .models import Invoice, InvoiceLine, Payment

CASHIER = ("cashier",)  # administrators can always do everything
ADMIN_ONLY = ()


class InvoiceListView(RoleRequiredMixin, View):
    allowed_roles = CASHIER

    def get(self, request):
        tab = request.GET.get("tab", "settle")
        q = request.GET.get("q", "").strip()
        qs = Invoice.objects.select_related("patient", "payer")
        if tab == "paid":
            qs = qs.filter(status="paid")
        elif tab == "void":
            qs = qs.filter(status="void")
        else:
            tab, qs = "settle", qs.filter(status__in=["draft", "open", "part_paid"])
        if q:
            qs = qs.filter(
                Q(number__icontains=q) | Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q)
                | Q(patient__hospital_number__icontains=q)
            )  # fmt: skip
        rows = list(qs[:50])
        return render(request, "billing/list.html", {"invoices": rows, "tab": tab, "q": q,
                      "tabs": [("settle", "To settle"), ("paid", "Paid"), ("void", "Void")]})  # fmt: skip


class VisitInvoiceView(RoleRequiredMixin, View):
    """Open a visit's invoice, bringing its charges up to date first."""

    allowed_roles = CASHIER

    def get(self, request, visit_pk):
        visit = get_object_or_404(Visit.objects.select_related("patient"), pk=visit_pk)
        services.sync_charges(visit)
        inv = visit.invoices.exclude(status="void").order_by("-id").first()
        if not inv:
            messages.info(request, "There are no charges for this visit yet.")
            return redirect("billing:list")
        return redirect("billing:detail", pk=inv.pk)


def _detail_context(invoice, **forms):
    ctx = {
        "invoice": invoice,
        "patient": invoice.patient,
        "lines": invoice.lines.select_related("service").order_by("created_at", "id"),
        "payments": invoice.payments.select_related("received_by"),
        "add_form": AddLineForm(),
        "pay_form": PaymentForm(initial={"amount": invoice.balance if invoice.balance > 0 else ""}),
        "refund_form": RefundForm(),
        "void_form": VoidForm(),
        "credit_form": CreditForm(),
        "mpesa_form": MpesaPromptForm(initial={"phone": invoice.patient.phone, "amount": invoice.balance}),
        "mpesa_ready": services.mpesa_configured(),
        "pending_mpesa": invoice.mpesa_requests.filter(status="pending"),
    }
    ctx.update(forms)
    return ctx


class InvoiceDetailView(RoleRequiredMixin, View):
    allowed_roles = CASHIER

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice.objects.select_related("patient", "payer"), pk=pk)
        if invoice.is_draft and invoice.visit_id:
            services.reprice_draft_lines(invoice.visit)
        log_access(invoice)
        return render(request, "billing/detail.html", _detail_context(invoice))


class InvoicePostView(RoleRequiredMixin, View):
    """Base for the small POST actions on an invoice. A refused step shows its reason and changes nothing."""

    allowed_roles = CASHIER
    http_method_names = ["post"]

    def post(self, request, pk, **kwargs):
        self.invoice = get_object_or_404(Invoice.objects.select_related("patient", "payer"), pk=pk)
        try:
            return self.act(request, **kwargs) or redirect("billing:detail", pk=self.invoice.pk)
        except services.BillingError as e:
            messages.error(request, str(e))
            return redirect("billing:detail", pk=self.invoice.pk)

    def bad_form(self, request, form):
        messages.error(request, "That was not saved: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("billing:detail", pk=self.invoice.pk)


class AddLineView(InvoicePostView):
    def act(self, request):
        form = AddLineForm(request.POST)
        if not form.is_valid():
            return self.bad_form(request, form)
        services.add_manual_line(
            self.invoice, form.cleaned_data["service"], form.cleaned_data["quantity"], request.user
        )


class RemoveLineView(InvoicePostView):
    def act(self, request, line_pk):
        line = get_object_or_404(InvoiceLine, pk=line_pk, invoice=self.invoice, is_removed=False)
        services.remove_manual_line(line, request.user, request.POST.get("reason", ""))


class FinaliseView(InvoicePostView):
    def act(self, request):
        services.finalise(self.invoice, request.user)
        messages.success(request, f"{self.invoice.number} is finalised. Take payment below.")


class PayView(InvoicePostView):
    def act(self, request):
        form = PaymentForm(request.POST)
        if not form.is_valid():
            return self.bad_form(request, form)
        d = form.cleaned_data
        pay = services.take_payment(
            self.invoice, d["method"], d["amount"], request.user, mpesa_code=d["mpesa_code"],
            reference=d["reference"], is_deposit=d["is_deposit"],
        )  # fmt: skip
        messages.success(request, f"Payment recorded. Receipt {pay.receipt_number}.")
        return redirect("billing:receipt", pay_pk=pay.pk)


class MpesaPromptView(InvoicePostView):
    def act(self, request):
        form = MpesaPromptForm(request.POST)
        if not form.is_valid():
            return self.bad_form(request, form)
        services.send_mpesa_prompt(self.invoice, form.cleaned_data["phone"], form.cleaned_data["amount"], request.user)
        messages.success(
            request, "The patient has been asked to pay on their phone. The payment appears here when they confirm."
        )


class RefundView(InvoicePostView):
    allowed_roles = ADMIN_ONLY

    def act(self, request):
        form = RefundForm(request.POST)
        if not form.is_valid():
            return self.bad_form(request, form)
        d = form.cleaned_data
        pay = services.refund(self.invoice, d["amount"], d["method"], d["reason"], request.user)
        messages.success(request, f"Refund recorded. Receipt {pay.receipt_number}.")


class VoidView(InvoicePostView):
    allowed_roles = ADMIN_ONLY

    def act(self, request):
        form = VoidForm(request.POST)
        if not form.is_valid():
            return self.bad_form(request, form)
        services.void_invoice(self.invoice, form.cleaned_data["reason"], request.user)
        messages.success(request, f"{self.invoice.number} is void.")


class CreditNoteView(InvoicePostView):
    allowed_roles = ADMIN_ONLY

    def act(self, request):
        form = CreditForm(request.POST)
        if not form.is_valid():
            return self.bad_form(request, form)
        services.add_credit_note(self.invoice, form.cleaned_data["amount"], form.cleaned_data["reason"], request.user)
        messages.success(request, "Credit note added.")


class InvoicePrintView(RoleRequiredMixin, View):
    allowed_roles = CASHIER

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice.objects.select_related("patient", "payer"), pk=pk)
        log_event("print", invoice, reason="Invoice")
        return render(request, "billing/print_invoice.html", _detail_context(invoice))


class ReceiptView(RoleRequiredMixin, View):
    allowed_roles = CASHIER

    def get(self, request, pay_pk):
        pay = get_object_or_404(Payment.objects.select_related("invoice__patient", "received_by"), pk=pay_pk)
        log_event("print", pay, reason="Receipt")
        return render(
            request, "billing/receipt.html", {"pay": pay, "invoice": pay.invoice, "patient": pay.invoice.patient}
        )


@csrf_exempt
@require_POST
def mpesa_callback(request, token):
    """Safaricom posts the result of a payment prompt here. The secret token in the address is the credential."""
    expected = settings.MPESA_CALLBACK_TOKEN
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Http404
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("bad json")
    services.apply_mpesa_callback(payload)
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
