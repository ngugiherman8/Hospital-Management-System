import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing import services as billing
from apps.pharmacy_rx.models import Drug, Prescription, PrescriptionItem

from .models import EXPIRY_WARNING_DAYS, StockBatch, StockMovement


def on_hand(drug, include_expired=False):
    qs = StockMovement.objects.filter(batch__drug=drug)
    if not include_expired:
        qs = qs.filter(batch__expiry_date__gte=timezone.localdate())
    return qs.aggregate(t=Sum("quantity"))["t"] or 0


def on_hand_map():
    """Usable stock (not expired) for every drug, in one query."""
    rows = (
        StockMovement.objects.filter(batch__expiry_date__gte=timezone.localdate())
        .values("batch__drug")
        .annotate(t=Sum("quantity"))
    )
    return {r["batch__drug"]: r["t"] for r in rows}


def stock_levels():
    """One row per active drug with its warnings."""
    today = timezone.localdate()
    soon = today + datetime.timedelta(days=EXPIRY_WARNING_DAYS)
    totals = on_hand_map()
    batches = {}
    for b in StockBatch.objects.select_related("drug"):
        qty = b.quantity
        if qty > 0:
            batches.setdefault(b.drug_id, []).append((b, qty))
    rows = []
    for drug in Drug.objects.filter(is_active=True):
        qty = totals.get(drug.pk, 0)
        mine = batches.get(drug.pk, [])
        rows.append(
            {
                "drug": drug,
                "on_hand": qty,
                "low": qty <= drug.reorder_level,
                "out": qty <= 0,
                "expiring": [b for b, _ in mine if today <= b.expiry_date <= soon],
                "expired": [(b, q) for b, q in mine if b.expiry_date < today],
            }
        )
    return rows


def receive_stock(receipt, drug, batch_number, expiry_date, quantity, unit_cost, user):
    if quantity <= 0:
        raise ValidationError("The quantity received must be more than zero.")
    if expiry_date < timezone.localdate():
        raise ValidationError("This batch has already expired.")
    with transaction.atomic():
        batch, _ = StockBatch.objects.get_or_create(
            drug=drug,
            batch_number=batch_number.strip(),
            expiry_date=expiry_date,
            defaults={"receipt": receipt, "unit_cost": unit_cost},
        )
        movement = StockMovement.objects.create(
            batch=batch, kind=StockMovement.Kind.RECEIVE, quantity=quantity, user=user, reason=receipt.number
        )
        if receipt.purchase_order_id:
            receipt.purchase_order.refresh_status()
    return movement


def adjust_stock(batch, quantity, user, reason, kind=StockMovement.Kind.ADJUST):
    """A correction or write-off, with a reason. Cannot take a batch below zero."""
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.quantity + quantity < 0:
            raise ValidationError(f"Only {batch.quantity} on hand in this batch.")
        return StockMovement.objects.create(batch=batch, kind=kind, quantity=quantity, reason=reason, user=user)


def remaining_to_dispense(item):
    done = -(item.movements.filter(kind=StockMovement.Kind.DISPENSE).aggregate(t=Sum("quantity"))["t"] or 0)
    returned = item.movements.filter(kind=StockMovement.Kind.RETURN).aggregate(t=Sum("quantity"))["t"] or 0
    return item.quantity - (done - returned)


def plan_batches(drug, quantity):
    """Which batches a dispense of `quantity` would come from: earliest expiry first, expired batches never."""
    plan = []
    need = quantity
    for batch in StockBatch.objects.filter(drug=drug, expiry_date__gte=timezone.localdate()).order_by(
        "expiry_date", "id"
    ):
        qty = batch.quantity
        if qty <= 0:
            continue
        take = min(qty, need)
        plan.append((batch, take))
        need -= take
        if need == 0:
            break
    return plan, need


def dispense(item, quantity, user):
    """Give `quantity` of a prescribed drug from the earliest-expiring batches and charge for it.

    Batches are locked while counting, so two pharmacists cannot take the same stock.
    """
    if quantity <= 0:
        raise ValidationError("The quantity must be more than zero.")
    with transaction.atomic():
        item = (
            PrescriptionItem.objects.select_for_update()
            .select_related("drug", "prescription__consultation__appointment__patient")
            .get(pk=item.pk)
        )
        left = remaining_to_dispense(item)
        if quantity > left:
            raise ValidationError(f"Only {left} left to dispense on this prescription line.")
        list(StockBatch.objects.select_for_update().filter(drug=item.drug).order_by("expiry_date", "id"))
        plan, short = plan_batches(item.drug, quantity)
        if short:
            raise ValidationError(f"Not enough usable stock: {quantity - short} on hand, {quantity} needed.")
        moves = [
            StockMovement.objects.create(
                batch=batch,
                kind=StockMovement.Kind.DISPENSE,
                quantity=-take,
                prescription_item=item,
                user=user,
                reason=f"Prescription {item.prescription_id}",
            )
            for batch, take in plan
        ]
        visit = item.prescription.consultation.appointment
        billing.post_charge(
            visit.patient,
            billing.ServiceItem.objects.filter(drug=item.drug, is_active=True).first(),
            quantity=quantity,
            appointment=visit,
            source_key=f"dispense:{moves[0].pk}",
            description=f"{item.drug} x{quantity}",
            user=user,
        )
        _refresh_prescription(item.prescription)
    return moves


def _refresh_prescription(prescription):
    items = list(prescription.items.all())
    left = [remaining_to_dispense(i) for i in items]
    Status = Prescription.Dispensing
    if items and all(n <= 0 for n in left):
        new = Status.DISPENSED
    elif any(n < i.quantity for n, i in zip(left, items, strict=True)):
        new = Status.PARTIAL
    else:
        new = Status.PENDING
    if new != prescription.dispensing_status:
        prescription.dispensing_status = new
        prescription.save(update_fields=["dispensing_status", "updated_at"])


def stock_take(counts, user, note="Stock-take"):
    """`counts` maps batch -> counted quantity. Differences become adjustments. Returns the differences."""
    result = []
    with transaction.atomic():
        for batch, counted in counts.items():
            batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
            system = batch.quantity
            diff = counted - system
            if diff:
                reason = f"{note} {timezone.localdate():%d %b %Y}: counted {counted}, system {system}"
                adjust_stock(batch, diff, user, reason)
            result.append((batch, system, counted, diff))
    return result


def day_stock_reconciliation(day):
    """Opening + received - dispensed +/- adjustments = closing, for every drug that moved on `day`."""
    rows = []
    moved = StockMovement.objects.filter(created_at__date=day).values_list("batch__drug", flat=True).distinct()
    for drug in Drug.objects.filter(pk__in=moved):
        before = on_hand(drug, include_expired=True) - (
            StockMovement.objects.filter(batch__drug=drug, created_at__date__gte=day).aggregate(t=Sum("quantity"))["t"]
            or 0
        )
        today = StockMovement.objects.filter(batch__drug=drug, created_at__date=day)
        by_kind = {k: today.filter(kind=k).aggregate(t=Sum("quantity"))["t"] or 0 for k in StockMovement.Kind.values}
        closing_calc = before + sum(by_kind.values())
        negative = any(b.quantity < 0 for b in StockBatch.objects.filter(drug=drug))
        rows.append({"drug": drug, "opening": before, **by_kind, "closing": closing_calc, "ok": not negative})
    return rows
