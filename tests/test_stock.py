import datetime
import threading
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.appointments.models import Appointment, Status
from apps.billing.models import InvoiceLine
from apps.clinical.models import Consultation
from apps.pharmacy_rx.models import Drug, Prescription, PrescriptionItem
from apps.stock import services
from apps.stock.models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, StockBatch, StockMovement, Supplier

TODAY = datetime.date.today


def days(n):
    return timezone.localdate() + datetime.timedelta(days=n)


@pytest.fixture
def drug(db, catalogue):
    call_command("seed_billing", verbosity=0)
    return Drug.objects.get(generic_name="Paracetamol")


@pytest.fixture
def receipt(db, pharmacist):
    return GoodsReceipt.objects.create(supplier=Supplier.objects.create(name="MEDS"), received_by=pharmacist)


@pytest.fixture
def stocked(drug, receipt, pharmacist):
    """Three batches: one expired, one expiring soonest, one later."""
    for number, expiry, qty in [("OLD", days(-5), 40), ("SOON", days(30), 10), ("LATER", days(300), 50)]:
        batch = StockBatch.objects.create(drug=drug, receipt=receipt, batch_number=number, expiry_date=expiry)
        StockMovement.objects.create(batch=batch, kind="receive", quantity=qty, user=pharmacist)
    return drug


def make_item(patient, drug, doctor, quantity):
    visit = Appointment.objects.create(patient=patient, kind="walk_in", status=Status.WITH_DOCTOR)
    consultation = Consultation.objects.create(appointment=visit, doctor=doctor)
    rx = Prescription.objects.create(consultation=consultation, prescribed_by=doctor)
    return PrescriptionItem.objects.create(
        prescription=rx, drug=drug, dose="1", frequency="tds", days=5, quantity=quantity
    )


def test_stock_movements_cannot_be_edited_or_deleted(stocked, pharmacist):
    move = StockMovement.objects.first()
    move.quantity = 999
    with pytest.raises(PermissionError):
        move.save()
    with pytest.raises(PermissionError):
        move.delete()
    with pytest.raises(PermissionError):
        StockMovement.objects.all().delete()
    with pytest.raises(PermissionError):
        StockBatch.objects.first().delete()


def test_movement_rules(stocked, pharmacist):
    batch = StockBatch.objects.get(batch_number="LATER")
    with pytest.raises(ValidationError):
        StockMovement.objects.create(batch=batch, kind="dispense", quantity=5)  # a dispense must be negative
    with pytest.raises(ValidationError):
        StockMovement.objects.create(batch=batch, kind="adjust", quantity=-1, reason="")  # reason needed
    with pytest.raises(ValidationError):
        services.adjust_stock(batch, -500, pharmacist, "too many")  # cannot go below zero


def test_quantity_on_hand_is_calculated_from_movements(stocked, pharmacist):
    batch = StockBatch.objects.get(batch_number="SOON")
    assert batch.quantity == 10
    services.adjust_stock(batch, -3, pharmacist, "Broken box")
    assert StockBatch.objects.get(pk=batch.pk).quantity == 7
    assert services.on_hand(stocked) == 7 + 50  # expired batch not counted


def test_dispense_takes_from_the_earliest_expiring_usable_batch(stocked, patient, doctor, pharmacist):
    item = make_item(patient, stocked, doctor, 25)
    services.dispense(item, 25, pharmacist)
    qty = {b.batch_number: b.quantity for b in StockBatch.objects.all()}
    assert qty == {"OLD": 40, "SOON": 0, "LATER": 35}  # expired batch untouched; SOON emptied first


def test_dispense_cannot_exceed_stock_or_the_prescription(stocked, patient, doctor, pharmacist):
    item = make_item(patient, stocked, doctor, 100)
    with pytest.raises(ValidationError):
        services.dispense(item, 100, pharmacist)  # only 60 usable
    assert services.on_hand(stocked) == 60
    with pytest.raises(ValidationError):
        services.dispense(make_item(patient, stocked, doctor, 5), 6, pharmacist)  # more than prescribed


def test_dispensing_reduces_stock_adds_the_charge_and_marks_the_prescription(stocked, patient, doctor, pharmacist):
    item = make_item(patient, stocked, doctor, 20)
    services.dispense(item, 8, pharmacist)
    item.prescription.refresh_from_db()
    assert item.prescription.dispensing_status == "partial"
    services.dispense(item, 12, pharmacist)
    item.prescription.refresh_from_db()
    assert item.prescription.dispensing_status == "dispensed"
    assert services.on_hand(stocked) == 40
    charges = InvoiceLine.objects.filter(description__startswith="Paracetamol")
    assert sorted(c.quantity for c in charges) == [8, 12] and sum(c.amount for c in charges) == D("400.00")
    with pytest.raises(ValidationError):
        services.dispense(item, 1, pharmacist)


@pytest.mark.django_db(transaction=True)
def test_two_pharmacists_cannot_take_the_same_stock(drug, patient, doctor, pharmacist, catalogue):
    receipt = GoodsReceipt.objects.create(supplier=Supplier.objects.create(name="M"), received_by=pharmacist)
    services.receive_stock(receipt, drug, "ONLY", days(200), 10, 1, pharmacist)
    items = [make_item(patient, drug, doctor, 10) for _ in range(2)]
    results, barrier = [], threading.Barrier(2)

    def work(item):
        try:
            barrier.wait()
            services.dispense(item, 10, pharmacist)
            results.append("ok")
        except ValidationError:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in items]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok", "refused"]
    assert StockBatch.objects.get().quantity == 0


def test_levels_warn_when_low_or_expiring(stocked, pharmacist):
    row = next(r for r in services.stock_levels() if r["drug"] == stocked)
    assert row["on_hand"] == 60 and not row["low"]
    assert [b.batch_number for b in row["expiring"]] == ["SOON"]
    assert [b.batch_number for b, _ in row["expired"]] == ["OLD"]
    stocked.reorder_level = 60
    stocked.save()
    assert next(r for r in services.stock_levels() if r["drug"] == stocked)["low"]
    empty = next(r for r in services.stock_levels() if r["drug"] != stocked)
    assert empty["out"] and empty["low"]


def test_receiving_rejects_expired_stock_and_updates_the_order(drug, pharmacist):
    supplier = Supplier.objects.create(name="S")
    order = PurchaseOrder.objects.create(supplier=supplier, status="sent")
    PurchaseOrderLine.objects.create(order=order, drug=drug, quantity=100, unit_cost=2)
    receipt = GoodsReceipt.objects.create(supplier=supplier, purchase_order=order, received_by=pharmacist)
    with pytest.raises(ValidationError):
        services.receive_stock(receipt, drug, "X", days(-1), 5, 1, pharmacist)
    services.receive_stock(receipt, drug, "A", days(100), 60, 2, pharmacist)
    order.refresh_from_db()
    assert order.status == "part"
    services.receive_stock(receipt, drug, "B", days(120), 40, 2, pharmacist)
    order.refresh_from_db()
    assert order.status == "received"


def test_stock_take_turns_differences_into_adjustments(stocked, pharmacist):
    soon, later = StockBatch.objects.get(batch_number="SOON"), StockBatch.objects.get(batch_number="LATER")
    result = services.stock_take({soon: 8, later: 50}, pharmacist)
    assert {r[0].batch_number: r[3] for r in result} == {"SOON": -2, "LATER": 0}
    assert soon.quantity == 8
    move = StockMovement.objects.filter(kind="adjust").get()
    assert "counted 8, system 10" in move.reason and move.user == pharmacist


# ---------- roles and screens ----------


def test_a_cashier_cannot_dispense_and_a_doctor_cannot_receive(login, stocked, patient, doctor, cashier, pharmacist):
    item = make_item(patient, stocked, doctor, 5)
    url = f"/stock/dispense/{item.prescription_id}/item/{item.pk}/"
    assert login(cashier).post(url, {"quantity": 5}).status_code == 403
    assert login(doctor).post(url, {"quantity": 5}).status_code == 403
    assert login(doctor).get("/stock/receipts/").status_code == 403
    assert services.on_hand(stocked) == 60
    assert login(pharmacist).post(url, {"quantity": 5}).status_code == 302
    assert services.on_hand(stocked) == 55


def test_who_sees_stock_levels(login, stocked, doctor, nurse, pharmacist, cashier, reception, lab, admin_user):
    for user, status in [(doctor, 200), (nurse, 200), (pharmacist, 200), (admin_user, 200), (cashier, 403),
                         (reception, 403), (lab, 403)]:  # fmt: skip
        assert login(user).get("/stock/").status_code == status, user.role


def test_pharmacist_pages_work(login, stocked, patient, doctor, pharmacist):
    item = make_item(patient, stocked, doctor, 5)
    c = login(pharmacist)
    for url in ["/stock/dispense/", f"/stock/dispense/{item.prescription_id}/", "/stock/receipts/", "/stock/orders/",
                "/stock/suppliers/", "/stock/adjust/", "/stock/take/"]:  # fmt: skip
        assert c.get(url).status_code == 200, url


def test_prescribers_see_stock_in_the_drug_list(login, stocked, doctor, patient):
    from apps.pharmacy_rx.forms import PrescribeForm

    form = PrescribeForm(patient=patient)
    label = form.fields["drug"].label_from_instance(stocked)
    assert "60 in stock" in label


def test_receive_screen_creates_a_batch(login, drug, pharmacist, receipt):
    r = login(pharmacist).post(
        f"/stock/receipts/{receipt.pk}/",
        {
            "drug": drug.pk,
            "batch_number": "NEW1",
            "expiry_date": days(365).isoformat(),
            "quantity": 30,
            "unit_cost": "2",
        },
    )
    assert r.status_code == 302
    assert StockBatch.objects.get(batch_number="NEW1").quantity == 30


def test_dispensing_a_drug_added_after_the_price_list_still_raises_a_charge_alert(
    stocked, patient, doctor, pharmacist, receipt
):
    """A drug with no billable item must not be dispensed silently: an unpriced item is made and an alert raised."""
    from apps.billing.models import ChargeAlert, ServiceItem

    new = Drug.objects.create(generic_name="Newdrug", strength="10 mg", form="Tablet")
    assert not ServiceItem.objects.filter(drug=new).exists()
    services.receive_stock(receipt, new, "N1", days(200), 10, 1, pharmacist)
    services.dispense(make_item(patient, new, doctor, 5), 5, pharmacist)  # dispensing is never blocked
    assert ServiceItem.objects.filter(drug=new).exists()
    assert ChargeAlert.objects.filter(resolved_at__isnull=True, source_key__startswith="dispense:").exists()
    assert not InvoiceLine.objects.filter(description__startswith="Newdrug").exists()


def test_sync_service_items_creates_missing_items_once(db, catalogue):
    from apps.billing.models import ServiceItem
    from apps.billing.services import sync_service_items

    Drug.objects.create(generic_name="Syncdrug", strength="5 mg", form="Tablet")
    first = sync_service_items()
    assert first >= 1 and ServiceItem.objects.filter(drug__generic_name="Syncdrug").count() == 1
    assert sync_service_items() == 0
