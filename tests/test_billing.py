import datetime
import json
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from apps.appointments.models import Appointment, Status
from apps.billing import services
from apps.billing.models import CashUp, ChargeAlert, Claim, Invoice, InvoiceLine, Payment, ServiceItem
from apps.clinical.models import Consultation, Diagnosis, TriageRecord
from apps.orders.models import Order, OrderItem, TestCatalogue
from apps.wards import models as wards
from apps.wards.models import Bed, Ward


@pytest.fixture
def priced(db, catalogue):
    call_command("seed_billing", verbosity=0)


@pytest.fixture
def visit(patient, priced):
    return Appointment.objects.create(patient=patient, kind="walk_in")


@pytest.fixture
def with_doctor(visit, nurse, doctor):
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Fever")
    visit.move_to(Status.WAITING_DOCTOR, nurse)
    visit.move_to(Status.WITH_DOCTOR, doctor)
    return visit


def draft(visit):
    return visit.invoices.get(status="draft")


def line_service(invoice, code):
    return invoice.active_lines.filter(service__code=code).first()


@pytest.fixture
def invoice(visit, cashier):
    """A finalised invoice of 1,000: registration 100 plus 900 of extra charges."""
    inv = services.draft_invoice_for(visit.patient, visit)  # walk-ins also get the registration fee (100)
    services.add_line(inv, ServiceItem.objects.get(code="CONS-GENERAL"), 1, cashier)  # 500
    services.add_line(inv, ServiceItem.objects.get(code="T-MAL-RDT"), 1, cashier)  # 500 -> total 1,100
    return services.finalise(inv, cashier)


# ---------- charges from clinical events ----------


def test_outpatient_case_produces_the_right_draft_charges(with_doctor, doctor, patient):
    from apps.pharmacy_rx.models import Drug

    consultation = Consultation.objects.create(appointment=with_doctor, doctor=doctor, outcome="home")
    Diagnosis.objects.create(consultation=consultation, icd10_code="B54", name="Malaria")
    order = Order.objects.create(appointment=with_doctor, ordered_by=doctor)
    OrderItem.objects.create(order=order, test=TestCatalogue.objects.get(code="MAL-RDT"))
    with_doctor.move_to(Status.COMPLETED, doctor)
    codes = set(draft(with_doctor).active_lines.values_list("service__code", flat=True))
    assert codes == {"REG", "CONS-GENERAL", "T-MAL-RDT"}
    assert draft(with_doctor).total == D("1100.00")  # 100 + 500 + 500
    assert Drug.objects.exists()


def test_the_same_event_is_never_charged_twice(with_doctor, doctor):
    consultation = Consultation.objects.create(appointment=with_doctor, doctor=doctor, outcome="home")
    Diagnosis.objects.create(consultation=consultation, icd10_code="B54", name="Malaria")
    with_doctor.move_to(Status.COMPLETED, doctor)
    with_doctor.save()
    with_doctor.save()
    assert InvoiceLine.objects.filter(source_key__startswith="cons:").count() == 1
    assert InvoiceLine.objects.filter(source_key__startswith="reg:").count() == 1


def test_registration_is_charged_once_per_patient(patient, priced, nurse):
    for _ in range(2):
        Appointment.objects.create(patient=patient, kind="walk_in", status=Status.WAITING_TRIAGE)
    assert InvoiceLine.objects.filter(service__code="REG").count() == 1


def test_cancelled_test_is_taken_off_a_draft_invoice(with_doctor, doctor):
    order = Order.objects.create(appointment=with_doctor, ordered_by=doctor)
    item = OrderItem.objects.create(order=order, test=TestCatalogue.objects.get(code="MAL-RDT"))
    assert line_service(draft(with_doctor), "T-MAL-RDT")
    item.status = OrderItem.Status.CANCELLED
    item.save()
    assert line_service(draft(with_doctor), "T-MAL-RDT") is None


def test_cancel_after_finalising_raises_an_alert(with_doctor, doctor, cashier):
    order = Order.objects.create(appointment=with_doctor, ordered_by=doctor)
    item = OrderItem.objects.create(order=order, test=TestCatalogue.objects.get(code="MAL-RDT"))
    services.finalise(draft(with_doctor), cashier)
    item.status = OrderItem.Status.CANCELLED
    item.save()
    assert ChargeAlert.objects.filter(resolved_at__isnull=True).exists()


def test_unpriced_service_raises_an_alert_and_does_not_block(priced, db):
    from apps.patients.models import Patient

    new = Patient.objects.create(first_name="A", last_name="B", date_of_birth=datetime.date(1990, 1, 1), sex="M")
    ServiceItem.objects.filter(code="REG").update(is_active=False)
    visit = Appointment.objects.create(patient=new, kind="walk_in", status=Status.WAITING_TRIAGE)
    assert visit.pk and ChargeAlert.objects.filter(appointment=visit).exists()


def test_payer_price_falls_back_to_self_pay(patient, priced):
    item = ServiceItem.objects.get(code="CONS-GENERAL")
    assert item.price_for("sha") == D("500.00")
    item.prices.create(payer_kind="sha", amount=300)
    assert ServiceItem.objects.get(code="CONS-GENERAL").price_for("sha") == D("300.00")


def test_bed_nights_are_charged_once_each(with_doctor, doctor, db):
    ward = Ward.objects.create(name="W1")
    bed = Bed.objects.create(ward=ward, number="1")
    admission = wards.admit(with_doctor, bed, "Severe malaria", doctor)
    admission.admitted_at = timezone.now() - datetime.timedelta(days=3)
    admission.save()
    assert services.charge_bed_nights(admission) == 3
    assert services.charge_bed_nights(admission) == 0
    assert InvoiceLine.objects.filter(source_key__startswith=f"bed:{admission.pk}:").count() == 3


def test_same_day_stay_is_one_bed_day(with_doctor, doctor):
    bed = Bed.objects.create(ward=Ward.objects.create(name="W2"), number="1")
    admission = wards.admit(with_doctor, bed, "Observation", doctor)
    Consultation.objects.create(appointment=with_doctor, doctor=doctor)
    wards.discharge(admission, "Fine.", doctor)
    assert InvoiceLine.objects.filter(source_key__startswith=f"bed:{admission.pk}:").count() == 1


# ---------- invoices ----------


def test_invoice_numbers_have_no_gaps_and_drafts_have_none(patient, priced, cashier):
    numbers = []
    for _ in range(3):
        visit = Appointment.objects.create(patient=patient, kind="walk_in")
        inv = services.draft_invoice_for(patient, visit)
        assert inv.number == ""
        services.add_line(inv, ServiceItem.objects.get(code="REG"), 1, cashier)
        numbers.append(int(services.finalise(inv, cashier).number.split("-")[-1]))
    assert numbers == [numbers[0], numbers[0] + 1, numbers[0] + 2]


def test_lines_only_change_before_finalising(invoice, cashier):
    with pytest.raises(ValidationError):
        services.add_line(invoice, ServiceItem.objects.get(code="REG"), 1, cashier)
    with pytest.raises(ValidationError):
        services.remove_line(invoice.lines.first(), cashier)
    with pytest.raises(ValidationError):
        services.finalise(invoice, cashier)


def test_an_empty_invoice_cannot_be_finalised(patient, priced, cashier):
    with pytest.raises(ValidationError):
        services.finalise(services.draft_invoice_for(patient), cashier)


def test_removed_line_is_kept_but_not_counted(visit, cashier):
    inv = draft(visit)
    line = services.add_line(inv, ServiceItem.objects.get(code="CONS-GENERAL"), 1, cashier)
    services.remove_line(line, cashier, "Charged by mistake")
    assert inv.total == D("100.00")
    assert inv.lines.count() == 2


def test_totals_balances_part_payments_and_refunds_add_up(invoice, cashier, admin_user):
    assert invoice.total == sum(line.amount for line in invoice.active_lines) == D("1100.00")
    services.take_payment(invoice, "cash", D("400"), cashier)
    invoice.refresh_from_db()
    assert (invoice.paid, invoice.balance, invoice.status) == (D("400.00"), D("700.00"), "part_paid")
    services.take_payment(invoice, "mpesa", D("700"), cashier, mpesa_code="QWE123")
    invoice.refresh_from_db()
    assert (invoice.balance, invoice.status) == (D("0.00"), "paid")
    services.refund(invoice, D("200"), admin_user, "Test not done")
    invoice.refresh_from_db()
    assert (invoice.paid, invoice.balance, invoice.status) == (D("900.00"), D("200.00"), "part_paid")
    assert invoice.paid == sum(p.amount for p in invoice.payments.all())


def test_payment_rules(invoice, cashier, admin_user):
    with pytest.raises(ValidationError):
        services.take_payment(invoice, "cash", D("1101"), cashier)  # more than the balance
    with pytest.raises(ValidationError):
        services.take_payment(invoice, "cash", D("0"), cashier)
    with pytest.raises(ValidationError):
        services.take_payment(invoice, "mpesa", D("10"), cashier)  # no code typed
    with pytest.raises(PermissionError):
        services.refund(invoice, D("10"), cashier, "no")
    with pytest.raises(ValidationError):
        services.refund(invoice, D("10"), admin_user, "nothing paid yet")


def test_payment_on_a_draft_is_refused(visit, cashier):
    with pytest.raises(ValidationError):
        services.take_payment(draft(visit), "cash", D("10"), cashier)


def test_receipt_numbers_are_unique_and_refunds_are_negative(invoice, cashier, admin_user):
    p1 = services.take_payment(invoice, "cash", D("100"), cashier)
    p2 = services.take_payment(invoice, "cash", D("100"), cashier)
    r = services.refund(invoice, D("50"), admin_user, "Overcharged")
    assert len({p1.receipt_number, p2.receipt_number, r.receipt_number}) == 3
    assert r.amount == D("-50.00") and r.receipt_number.startswith("RF-") and r.approved_by == admin_user


def test_void_needs_an_administrator_a_reason_and_no_payments(invoice, cashier, admin_user):
    with pytest.raises(PermissionError):
        services.void_invoice(invoice, cashier, "mistake")
    with pytest.raises(ValidationError):
        services.void_invoice(invoice, admin_user, " ")
    services.take_payment(invoice, "cash", D("100"), cashier)
    with pytest.raises(ValidationError):
        services.void_invoice(invoice, admin_user, "Wrong patient")
    services.refund(invoice, D("100"), admin_user, "Wrong patient")
    services.void_invoice(invoice, admin_user, "Wrong patient")
    invoice.refresh_from_db()
    assert invoice.status == "void" and invoice.void_reason and invoice.voided_by == admin_user


# ---------- nothing is edited or deleted ----------


def test_payments_invoices_and_lines_cannot_be_edited_or_deleted(invoice, cashier):
    payment = services.take_payment(invoice, "cash", D("100"), cashier)
    payment.amount = D("1")
    with pytest.raises(PermissionError):
        payment.save()
    with pytest.raises(PermissionError):
        payment.delete()
    with pytest.raises(PermissionError):
        Payment.objects.all().delete()
    with pytest.raises(PermissionError):
        Payment.objects.update(amount=1)
    with pytest.raises(PermissionError):
        invoice.delete()
    with pytest.raises(PermissionError):
        invoice.lines.first().delete()


def test_admin_site_offers_no_edit_or_delete_of_money_or_stock(admin_user, rf):
    from django.contrib import admin

    from apps.billing.models import Claim as C
    from apps.stock.models import StockMovement

    request = rf.get("/")
    request.user = admin_user
    admin_user.is_superuser = True
    for model in (Payment, Invoice, CashUp, C, StockMovement):
        ma = admin.site._registry[model]
        assert not ma.has_change_permission(request)
        assert not ma.has_delete_permission(request)
        assert not ma.has_add_permission(request)


# ---------- M-Pesa ----------


def test_the_same_mpesa_code_cannot_be_typed_twice(invoice, cashier):
    services.take_payment(invoice, "mpesa", D("100"), cashier, mpesa_code="abc123")
    with pytest.raises(ValidationError):
        services.take_payment(invoice, "mpesa", D("100"), cashier, mpesa_code="ABC123")
    assert invoice.payments.count() == 1


def test_a_repeated_mpesa_callback_creates_one_payment(invoice):
    first, created1 = services.record_mpesa_confirmation(invoice.number, "RKT55AA", D("300"))
    second, created2 = services.record_mpesa_confirmation(invoice.number, "RKT55AA", D("300"))
    assert created1 and not created2 and first.pk == second.pk
    assert Payment.objects.filter(mpesa_code="RKT55AA").count() == 1
    invoice.refresh_from_db()
    assert invoice.paid == D("300.00") and invoice.status == "part_paid"


def test_mpesa_endpoint_checks_the_secret_and_handles_repeats(client, invoice, settings):
    url = "/billing/mpesa/confirm/"
    body = json.dumps({"invoice_number": invoice.number, "code": "ZZ1", "amount": "100"})
    assert client.post(url, body, content_type="application/json").status_code == 404  # switched off
    settings.MPESA_CALLBACK_SECRET = "s3cret"
    assert client.post(url, body, content_type="application/json").status_code == 403
    for _ in range(2):
        r = client.post(url, body, content_type="application/json", headers={"X-Mpesa-Secret": "s3cret"})
        assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert Payment.objects.filter(mpesa_code="ZZ1").count() == 1
    bad = client.post(url, "{}", content_type="application/json", headers={"X-Mpesa-Secret": "s3cret"})
    assert bad.status_code == 400


# ---------- claims ----------


@pytest.fixture
def sha_invoice(patient, priced, cashier):
    patient.payer, patient.member_number = "sha", "SHA-001"
    patient.save()
    visit = Appointment.objects.create(patient=patient, kind="walk_in")
    services.post_charge(patient, ServiceItem.objects.get(code="CONS-GENERAL"), appointment=visit, source_key="c1")
    return services.finalise(draft(visit), cashier)


def test_claim_is_built_from_the_invoice_and_cash_invoices_have_none(invoice, sha_invoice, cashier):
    claim = services.create_claim(sha_invoice, cashier)
    assert claim.amount_claimed == sha_invoice.total and claim.lines.count() == 1
    assert claim.member_number == "SHA-001" and claim.status == "draft"
    with pytest.raises(ValidationError):
        services.create_claim(sha_invoice, cashier)  # only one claim per invoice
    with pytest.raises(ValidationError):
        services.create_claim(invoice, cashier)  # self-pay


def test_rejected_claim_can_be_corrected_and_resubmitted(sha_invoice, cashier):
    claim = services.create_claim(sha_invoice, cashier)
    services.export_claims(Claim.objects.all())
    claim.refresh_from_db()
    assert claim.status == "submitted" and claim.submitted_at
    services.reject_claim(claim, "Member number invalid")
    with pytest.raises(ValidationError):
        services.reject_claim(claim, "again")  # already rejected
    services.correct_claim(claim, "SHA-002", "B54 Malaria")
    claim.refresh_from_db()
    assert (claim.status, claim.member_number, claim.resubmissions) == ("draft", "SHA-002", 1)
    csv_text = services.export_claims(Claim.objects.all())
    assert "SHA-002" in csv_text
    claim.refresh_from_db()
    assert claim.status == "submitted"


def test_a_month_of_claims_exports_in_one_file(client, login, cashier, patient, priced):
    patient.payer = "insurer"
    patient.save()
    for n in range(3):
        v = Appointment.objects.create(patient=patient, kind="walk_in")
        services.post_charge(patient, ServiceItem.objects.get(code="CONS-GENERAL"), appointment=v, source_key=f"m{n}")
        services.create_claim(services.finalise(draft(v), cashier), cashier)
    month = f"{timezone.localdate():%Y-%m}"
    r = login(cashier).get(f"/billing/claims/export/?month={month}")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    body = r.content.decode().strip().splitlines()
    assert len(body) == 5  # header plus 4 lines: registration and a consultation, then two consultations
    assert Claim.objects.filter(status="submitted").count() == 3


def test_paid_claim_lowers_the_invoice_balance(sha_invoice, cashier):
    claim = services.create_claim(sha_invoice, cashier)
    services.export_claims(Claim.objects.all())
    claim.refresh_from_db()
    services.approve_claim(claim, D("600"))
    services.mark_claim_paid(claim, cashier, D("600"))
    sha_invoice.refresh_from_db()
    claim.refresh_from_db()
    assert claim.status == "paid" and sha_invoice.status == "paid" and sha_invoice.balance == 0
    assert sha_invoice.payments.get().method == "insurer"


# ---------- cash-up and the end of the day ----------


def test_cash_up_compares_counted_with_expected(invoice, cashier):
    services.take_payment(invoice, "cash", D("400"), cashier)
    services.take_payment(invoice, "mpesa", D("200"), cashier, mpesa_code="MP1")
    assert services.expected_for(cashier, timezone.localdate())["cash"] == "400.00"
    with pytest.raises(ValidationError):  # 50 short and no explanation
        services.close_day(cashier, timezone.localdate(), {"cash": 350, "mpesa": 200})
    cu = services.close_day(cashier, timezone.localdate(), {"cash": 350, "mpesa": 200}, "Gave change to a patient")
    assert cu.difference == D("-50.00")
    with pytest.raises(ValidationError):
        services.close_day(cashier, timezone.localdate(), {})  # already closed
    with pytest.raises(ValidationError):  # no more payments once the day is closed
        services.take_payment(invoice, "cash", D("10"), cashier)


def test_a_full_day_matches_the_ledger(priced, patient, cashier, pharmacist, doctor, admin_user):
    """The exit gate: the cashier and the pharmacist close a day and every total matches."""
    from apps.pharmacy_rx.models import Drug, Prescription, PrescriptionItem
    from apps.stock import services as stock
    from apps.stock.models import GoodsReceipt, StockMovement, Supplier

    drug = Drug.objects.get(generic_name="Paracetamol")
    receipt = GoodsReceipt.objects.create(supplier=Supplier.objects.create(name="MEDS"), received_by=pharmacist)
    stock.receive_stock(receipt, drug, "B1", timezone.localdate() + datetime.timedelta(days=400), 100, 2, pharmacist)
    visit = Appointment.objects.create(patient=patient, kind="walk_in", status=Status.WITH_DOCTOR)
    consultation = Consultation.objects.create(appointment=visit, doctor=doctor)
    rx = Prescription.objects.create(consultation=consultation, prescribed_by=doctor)
    item = PrescriptionItem.objects.create(prescription=rx, drug=drug, dose="1", frequency="tds", days=5, quantity=15)
    stock.dispense(item, 15, pharmacist)
    inv = services.finalise(draft(visit), cashier)  # registration 100 + 15 x 20 = 400
    assert inv.total == D("400.00")
    services.take_payment(inv, "cash", D("250"), cashier)
    services.take_payment(inv, "mpesa", D("150"), cashier, mpesa_code="FULL1")
    services.close_day(cashier, timezone.localdate(), {"cash": 250, "mpesa": 150})
    r = services.day_reconciliation(timezone.localdate())
    assert r["ledger_ok"] and r["net"] == r["billed"] == D("400.00")
    assert not r["unclosed_cashiers"] and not r["unexplained"] and r["difference_total"] == 0
    rows = stock.day_stock_reconciliation(timezone.localdate())
    assert rows[0]["opening"] == 0 and rows[0]["receive"] == 100 and rows[0]["dispense"] == -15
    assert rows[0]["closing"] == 85 == stock.on_hand(drug) and rows[0]["ok"]
    assert StockMovement.objects.filter(prescription_item=item).count() == 1
    inv.refresh_from_db()
    assert inv.status == "paid"


# ---------- roles ----------


def test_who_can_open_invoices(client, login, invoice, cashier, admin_user, doctor, nurse, pharmacist, reception):
    url = f"/billing/invoice/{invoice.pk}/"
    for user, status in [
        (cashier, 200),
        (admin_user, 200),
        (doctor, 403),
        (nurse, 403),
        (pharmacist, 403),
        (reception, 403),
    ]:
        assert login(user).get(url).status_code == status, user.role
    client.logout()
    assert client.get(url).status_code == 302


def test_cashier_cannot_void_refund_or_see_the_day_report(login, invoice, cashier, admin_user):
    services.take_payment(invoice, "cash", D("100"), cashier)
    c = login(cashier)
    assert c.post(f"/billing/invoice/{invoice.pk}/void/", {"reason": "x"}).status_code == 302
    assert (
        c.post(f"/billing/invoice/{invoice.pk}/refund/", {"amount": "10", "reason": "x", "method": "cash"}).status_code
        == 302
    )
    invoice.refresh_from_db()
    assert invoice.status != "void" and invoice.payments.filter(amount__lt=0).count() == 0
    assert c.get("/billing/day/").status_code == 403
    assert login(admin_user).get("/billing/day/").status_code == 200


def test_cashier_pages_work(login, invoice, cashier, visit):
    c = login(cashier)
    for url in ["/billing/invoices/", f"/billing/invoice/{invoice.pk}/print/", "/billing/prices/", "/billing/cash-up/",
                "/billing/claims/", f"/billing/visit/{visit.pk}/"]:  # fmt: skip
        r = c.get(url, follow=True)
        assert r.status_code == 200, url


def test_cashier_closes_the_day_through_the_screen(login, invoice, cashier):
    services.take_payment(invoice, "cash", D("100"), cashier)
    r = login(cashier).post("/billing/cash-up/", {"cash": "100", "mpesa": "0", "card": "0", "deposit": "0"})
    assert r.status_code == 302
    assert CashUp.objects.get(cashier=cashier).difference == 0


def test_invoice_screen_actions(login, visit, cashier):
    inv = draft(visit)
    c = login(cashier)
    c.post(
        f"/billing/invoice/{inv.pk}/add-line/",
        {"service": ServiceItem.objects.get(code="CONS-GENERAL").pk, "quantity": 2},
    )
    assert inv.total == D("1100.00")
    c.post(f"/billing/invoice/{inv.pk}/finalise/")
    inv.refresh_from_db()
    assert inv.status == "open" and inv.number
    c.post(f"/billing/invoice/{inv.pk}/pay/", {"method": "cash", "amount": "100"})
    inv.refresh_from_db()
    assert inv.status == "part_paid"
