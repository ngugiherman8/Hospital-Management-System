from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core.audit import log_access
from apps.core.permissions import role_required
from apps.pharmacy_rx.models import Prescription

from . import services
from .forms import AdjustForm, POLineForm, PurchaseOrderForm, ReceiptForm, ReceiptLineForm, SupplierForm
from .models import GoodsReceipt, PurchaseOrder, StockBatch, StockMovement, Supplier

PHARMACY = (Role.PHARMACIST,)
SEE_LEVELS = (Role.PHARMACIST, Role.DOCTOR, Role.NURSE, Role.DENTIST, Role.MIDWIFE)


def _errors(request, exc):
    messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


@role_required(*SEE_LEVELS)
def levels(request):
    rows = services.stock_levels()
    return render(
        request,
        "stock/levels.html",
        {
            "rows": rows,
            "low": [r for r in rows if r["low"]],
            "expiring": [r for r in rows if r["expiring"] or r["expired"]],
            "can_work": request.user.has_role(*PHARMACY),
        },
    )


@role_required(*PHARMACY)
def dispense_queue(request):
    pending = (
        Prescription.objects.filter(dispensing_status__in=["pending", "partial"])
        .select_related("consultation__appointment__patient")
        .order_by("id")
    )
    return render(request, "stock/dispense_queue.html", {"prescriptions": pending})


@role_required(*PHARMACY)
def dispense_detail(request, pk):
    rx = get_object_or_404(Prescription.objects.select_related("consultation__appointment__patient"), pk=pk)
    log_access(rx)
    rows = []
    for item in rx.items.select_related("drug"):
        left = services.remaining_to_dispense(item)
        plan, short = services.plan_batches(item.drug, left) if left > 0 else ([], 0)
        rows.append({"item": item, "left": left, "plan": plan, "short": short, "on_hand": services.on_hand(item.drug)})
    patient = rx.consultation.appointment.patient
    return render(request, "stock/dispense_detail.html", {"rx": rx, "rows": rows, "patient": patient})


@role_required(*PHARMACY)
@require_POST
def dispense_item(request, pk, item_pk):
    rx = get_object_or_404(Prescription, pk=pk)
    item = get_object_or_404(rx.items.all(), pk=item_pk)
    try:
        quantity = int(request.POST.get("quantity", ""))
        moves = services.dispense(item, quantity, request.user)
    except ValueError:
        messages.error(request, "Type the quantity to dispense.")
    except ValidationError as exc:
        _errors(request, exc)
    else:
        messages.success(request, f"Dispensed {-sum(m.quantity for m in moves)} of {item.drug}.")
    return redirect("dispense_detail", pk=rx.pk)


# ------------------------------------------------------------ receiving


@role_required(*PHARMACY)
def receipt_list(request):
    return render(
        request,
        "stock/receipt_list.html",
        {"receipts": GoodsReceipt.objects.select_related("supplier")[:100], "form": ReceiptForm()},
    )


@role_required(*PHARMACY)
@require_POST
def receipt_create(request):
    po = None
    if request.POST.get("po"):
        po = get_object_or_404(PurchaseOrder, pk=request.POST["po"])
        form = ReceiptForm({"supplier": po.supplier_id, "supplier_invoice": request.POST.get("supplier_invoice", "")})
    else:
        form = ReceiptForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a supplier.")
        return redirect("receipt_list")
    receipt = form.save(commit=False)
    receipt.purchase_order = po
    receipt.received_by = request.user
    receipt.save()
    return redirect("receipt_detail", pk=receipt.pk)


@role_required(*PHARMACY)
def receipt_detail(request, pk):
    receipt = get_object_or_404(GoodsReceipt.objects.select_related("supplier", "purchase_order"), pk=pk)
    if request.method == "POST":
        form = ReceiptLineForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.receive_stock(
                    receipt, d["drug"], d["batch_number"], d["expiry_date"], d["quantity"], d["unit_cost"], request.user
                )
            except ValidationError as exc:
                _errors(request, exc)
            else:
                messages.success(request, f"Received {d['quantity']} of {d['drug']}.")
                return redirect("receipt_detail", pk=receipt.pk)
    else:
        form = ReceiptLineForm()
    movements = StockMovement.objects.filter(kind="receive", reason=receipt.number).select_related("batch__drug")
    return render(request, "stock/receipt_detail.html", {"receipt": receipt, "form": form, "movements": movements})


# ------------------------------------------------------------ purchase orders and suppliers


@role_required(*PHARMACY)
def order_list(request):
    return render(
        request,
        "stock/order_list.html",
        {"orders": PurchaseOrder.objects.select_related("supplier")[:100], "form": PurchaseOrderForm()},
    )


@role_required(*PHARMACY)
@require_POST
def order_create(request):
    form = PurchaseOrderForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a supplier.")
        return redirect("order_list")
    order = form.save(commit=False)
    order.created_by = request.user
    order.save()
    return redirect("order_detail", pk=order.pk)


@role_required(*PHARMACY)
def order_detail(request, pk):
    order = get_object_or_404(PurchaseOrder.objects.select_related("supplier"), pk=pk)
    if request.method == "POST":
        form = POLineForm(request.POST)
        if order.status != PurchaseOrder.Status.DRAFT:
            messages.error(request, "Lines can only be added while the order is a draft.")
        elif form.is_valid():
            line = form.save(commit=False)
            line.order = order
            line.save()
            return redirect("order_detail", pk=order.pk)
    else:
        form = POLineForm()
    lines = [(line, line.received) for line in order.lines.select_related("drug")]
    return render(request, "stock/order_detail.html", {"order": order, "lines": lines, "form": form})


@role_required(*PHARMACY)
@require_POST
def order_status(request, pk, action):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if action == "send" and order.status == "draft" and order.lines.exists():
        order.status = PurchaseOrder.Status.SENT
    elif action == "cancel" and order.status in ("draft", "sent") and not order.receipts.exists():
        order.status = PurchaseOrder.Status.CANCELLED
    else:
        messages.error(request, "That change is not possible for this order.")
        return redirect("order_detail", pk=order.pk)
    order.save()
    return redirect("order_detail", pk=order.pk)


@role_required(*PHARMACY)
def supplier_list(request):
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("supplier_list")
    return render(request, "stock/supplier_list.html", {"suppliers": Supplier.objects.all(), "form": form})


# ------------------------------------------------------------ adjustments and stock-take


@role_required(*PHARMACY)
def adjust(request):
    form = AdjustForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.adjust_stock(d["batch"], form.signed_quantity(), request.user, d["reason"], d["kind"])
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, "Stock updated.")
            return redirect("stock_adjust")
    recent = StockMovement.objects.filter(kind__in=["adjust", "expired"]).select_related("batch__drug", "user")[:30]
    return render(request, "stock/adjust.html", {"form": form, "recent": recent})


@role_required(*PHARMACY)
def take(request):
    batches = [b for b in StockBatch.objects.select_related("drug") if b.quantity != 0 or not b.expired]
    results = None
    if request.method == "POST":
        counts = {}
        for batch in batches:
            raw = request.POST.get(f"count_{batch.pk}", "").strip()
            if raw != "":
                try:
                    counts[batch] = max(int(raw), 0)
                except ValueError:
                    messages.error(request, f"{batch}: type a whole number.")
                    counts = None
                    break
        if counts is not None:
            results = services.stock_take(counts, request.user)
            messages.success(request, f"{len(results)} batch(es) counted, {sum(1 for r in results if r[3])} differ.")
            batches = [b for b in StockBatch.objects.select_related("drug") if b.quantity != 0 or not b.expired]
    rows = [(b, b.quantity) for b in batches]
    return render(request, "stock/take.html", {"rows": rows, "results": results, "today": timezone.localdate()})
