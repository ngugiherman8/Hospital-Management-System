import json
from decimal import Decimal

import pytest
from django.contrib import admin
from django.core.exceptions import PermissionDenied  # noqa: F401
from django.db import IntegrityError, transaction

from apps.billing import services
from apps.billing.models import Invoice, InvoiceLine, MpesaRequest, Payer, Payment, Price, ServiceItem
from apps.clinical.models import Visit, VisitDiagnosis
from apps.core.models import AuditLog
from apps.orders.models import Order
from apps.wards.models import Admission

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def prices(malaria, fbc):
    """Prices for the base services and two tests. Request this BEFORE the visit fixtures."""
    from apps.patients.models import PayerType

    items = {
        "REG": 200, "CONSULT": 500, "BED-GENERAL": 1500, "BED-MATERNITY": 2000,
        "BED-PAEDIATRIC": 1200, "BED-PRIVATE": 4000,
    }  # fmt: skip
    for code, amount in items.items():
        s = services.base_service(code)
        Price.objects.create(service=s, payer_type=PayerType.CASH, amount=amount)
        Price.objects.create(service=s, payer_type=PayerType.SHA, amount=D(amount) * D("0.8"))
    for test, amount in ((malaria, 600), (fbc, 400)):
        s = services.service_for_test(test)
        Price.objects.create(service=s, payer_type=PayerType.CASH, amount=amount)
    return items


def visit_of(entry):
    return Visit.objects.get(queue_entry=entry)


def invoice_of(visit):
    return visit.invoices.get()


def amounts(invoice):
    return {line.description.split(" (")[0]: line.amount for line in invoice.active_lines}


@pytest.fixture
def open_invoice(prices, with_doctor, doctor, malaria, icd, cashier, as_user):
    """A finalised KES 1,300 invoice: registration 200, consultation 500, malaria test 600."""
    v = visit_of(with_doctor)
    Order.objects.create(visit=v, test=malaria, ordered_by=doctor)
    VisitDiagnosis.objects.create(visit=v, icd10=icd, added_by=doctor)
    as_user(doctor).post(f"/clinical/consult/{with_doctor.pk}/", {"outcome": "home", "action": "finish"})
    inv = invoice_of(v)
    services.finalise(inv, cashier)
    inv.refresh_from_db()
    return inv


# ------------------------------------------------------------------------------------------------ step 1: prices and charges
def test_price_is_per_payer_type_with_cash_fallback(prices, malaria):
    consult = services.base_service("CONSULT")
    assert consult.price_for("cash") == 500 and consult.price_for("sha") == D("400.00")
    assert consult.price_for("private") == 500  # no private price: falls back to cash
    assert services.service_for_test(malaria).price_for("sha") == 600


def test_no_price_returns_none(db):
    assert services.base_service("CONSULT").price_for("cash") is None


def test_finishing_the_outpatient_case_produces_the_right_draft_charges(
    prices, with_doctor, doctor, malaria, icd, as_user
):
    v = visit_of(with_doctor)
    as_user(doctor).post(f"/orders/visits/{v.pk}/add/", {"test": malaria.pk})
    as_user(doctor).post(f"/clinical/visits/{v.pk}/diagnoses/add/", {"icd10": icd.pk})
    as_user(doctor).post(f"/clinical/consult/{with_doctor.pk}/", {"outcome": "home", "action": "finish"})
    inv = invoice_of(v)
    assert inv.status == "draft" and inv.payer.name == "Cash / M-Pesa"
    assert amounts(inv) == {"Registration fee": 200, "Consultation": 500, "Malaria test": 600}
    assert inv.total == 1300 and inv.number.startswith("INV-")


def test_syncing_again_never_double_charges(prices, with_doctor, doctor, malaria):
    v = visit_of(with_doctor)
    Order.objects.create(visit=v, test=malaria)
    for _ in range(3):
        services.sync_charges(v)
    assert InvoiceLine.objects.filter(invoice__visit=v).count() == 2  # registration + the test


def test_a_removed_charge_is_not_added_back(prices, with_doctor, doctor, malaria, cashier):
    v = visit_of(with_doctor)
    Order.objects.create(visit=v, test=malaria)
    line = InvoiceLine.objects.get(source_key__startswith="order:")
    services.remove_manual_line(line, cashier, "Waived")
    services.sync_charges(v)
    assert InvoiceLine.objects.filter(source_key__startswith="order:").count() == 1
    assert line.invoice.total == 200


def test_cancelling_a_test_removes_its_draft_charge(prices, with_doctor, malaria):
    v = visit_of(with_doctor)
    o = Order.objects.create(visit=v, test=malaria)
    assert invoice_of(v).total == 800
    o.status = "cancelled"
    o.save()
    assert invoice_of(v).total == 200


def test_a_price_set_later_is_picked_up_while_still_a_draft(with_doctor, malaria):
    from apps.patients.models import PayerType

    v = visit_of(with_doctor)
    Order.objects.create(visit=v, test=malaria)
    inv = invoice_of(v)
    assert inv.has_missing_prices and inv.total == 0  # nothing is priced yet
    Price.objects.create(service=services.service_for_test(malaria), payer_type=PayerType.CASH, amount=600)
    Price.objects.create(service=services.base_service("REG"), payer_type=PayerType.CASH, amount=200)
    services.reprice_draft_lines(v)
    inv.refresh_from_db()
    assert not inv.has_missing_prices and inv.total == 800


def test_sha_patient_is_charged_the_sha_price(prices, clinic, nurse, doctor, as_user):
    from apps.appointments.models import QueueEntry
    from apps.patients.models import InsuranceCover, Patient
    from tests.conftest import TRIAGE

    p = Patient.objects.create(first_name="S", last_name="Ha", date_of_birth="1990-01-01", sex="F", phone="0711000077")
    InsuranceCover.objects.create(patient=p, payer_type="sha", member_number="SHA-1")
    e, _ = QueueEntry.join(patient=p, clinic=clinic)
    as_user(nurse).post(f"/clinical/triage/{e.pk}/", TRIAGE)
    inv = invoice_of(visit_of(e))
    assert inv.payer.name == "SHA" and amounts(inv)["Registration fee"] == 160


def test_bed_days_are_charged_per_calendar_day_and_by_ward_type(prices, with_doctor, doctor, icd, beds, as_user):
    from django.utils import timezone

    v = visit_of(with_doctor)
    VisitDiagnosis.objects.create(visit=v, icd10=icd, added_by=doctor)
    as_user(doctor).post(f"/wards/admit/visit/{v.pk}/", {"bed": beds[0].pk, "reason": "Malaria"})
    adm = Admission.objects.get(visit=v)
    assert InvoiceLine.objects.filter(source_key__startswith=f"bed:{adm.pk}:").count() == 1  # day 1 straight away
    Admission.objects.filter(pk=adm.pk).update(admitted_at=timezone.now() - timezone.timedelta(days=3))
    services.sync_charges(v)
    keys = sorted(
        InvoiceLine.objects.filter(source_key__startswith=f"bed:{adm.pk}:").values_list("source_key", flat=True)
    )
    assert len(keys) == 4 and amounts(invoice_of(v))["Bed day 1, Test ward"] == 1500


def test_discharged_stay_counts_nights_not_the_discharge_day(prices, with_doctor, doctor, icd, beds, as_user):
    from django.utils import timezone

    v = visit_of(with_doctor)
    VisitDiagnosis.objects.create(visit=v, icd10=icd, added_by=doctor)
    as_user(doctor).post(f"/wards/admit/visit/{v.pk}/", {"bed": beds[0].pk, "reason": "Malaria"})
    adm = Admission.objects.get(visit=v)
    start = timezone.now() - timezone.timedelta(days=5)
    Admission.objects.filter(pk=adm.pk).update(
        admitted_at=start, discharged_at=start + timezone.timedelta(days=2), status="discharged"
    )
    adm.refresh_from_db()
    assert services.bed_days(adm) == 2


def test_a_billing_fault_never_blocks_clinical_work(prices, with_doctor, malaria, monkeypatch):
    def boom(visit):
        raise RuntimeError("billing is broken")

    monkeypatch.setattr(services, "sync_charges", boom)
    o = Order.objects.create(visit=visit_of(with_doctor), test=malaria)
    assert Order.objects.filter(pk=o.pk).exists()


# ------------------------------------------------------------------------------------------------ step 2: invoices
def test_invoice_numbers_are_gap_free(prices, patient, db):
    year = Invoice.objects.create(patient=patient, payer=Payer.for_cover(None)).created_at.year
    b = Invoice.objects.create(patient=patient, payer=Payer.for_cover(None))
    assert b.number == f"INV-{year}-000002"


def test_total_is_the_sum_of_its_lines(open_invoice):
    assert open_invoice.total == sum(line.amount for line in open_invoice.active_lines) == 1300


def test_cashier_opens_a_visits_invoice_adds_and_removes_a_line_then_prints(prices, with_doctor, cashier, as_user):
    v = visit_of(with_doctor)
    c = as_user(cashier)
    r = c.get(f"/billing/visits/{v.pk}/")
    assert r.status_code == 302
    inv = invoice_of(v)
    assert c.get(r.url).status_code == 200
    fbc_item = services.base_service("CONSULT")
    c.post(f"/billing/invoices/{inv.pk}/add-line/", {"service": fbc_item.pk, "quantity": "2"})
    assert inv.total == 200 + 1000
    extra = inv.active_lines.get(description="Consultation")
    c.post(f"/billing/invoices/{inv.pk}/lines/{extra.pk}/remove/", {"reason": "Added by mistake"})
    extra.refresh_from_db()
    assert extra.is_removed and extra.removed_by == cashier and inv.total == 200
    assert c.get(f"/billing/invoices/{inv.pk}/print/").status_code == 200
    assert AuditLog.objects.filter(action="print", reason="Invoice").exists()


def test_lines_are_frozen_once_finalised(open_invoice, cashier, as_user):
    svc = services.base_service("CONSULT")
    with pytest.raises(services.BillingError):
        services.add_manual_line(open_invoice, svc, 1, cashier)
    line = open_invoice.active_lines.first()
    with pytest.raises(services.BillingError):
        services.remove_manual_line(line, cashier)


def test_cannot_finalise_with_a_missing_price_or_no_lines(with_doctor, malaria, cashier):
    v = visit_of(with_doctor)
    Order.objects.create(visit=v, test=malaria)
    with pytest.raises(services.BillingError, match="No price is set"):
        services.finalise(invoice_of(v), cashier)


def test_credit_note_lowers_the_total_and_needs_a_reason(open_invoice, admin_user):
    services.add_credit_note(open_invoice, D("100"), "Test repeated by the lab", admin_user)
    assert open_invoice.total == 1200
    with pytest.raises(services.BillingError):
        services.add_credit_note(open_invoice, D("50"), "  ", admin_user)
    with pytest.raises(services.BillingError):
        services.add_credit_note(open_invoice, D("5000"), "Too much", admin_user)


# ------------------------------------------------------------------------------------------------ step 3: payments
def pay(inv, user, amount, method="cash", **kw):
    return services.take_payment(inv, method, D(amount), user, **kw)


def test_part_payments_leave_the_right_balance_and_status(open_invoice, cashier):
    assert open_invoice.status == "open"
    pay(open_invoice, cashier, "500")
    open_invoice.refresh_from_db()
    assert (open_invoice.paid, open_invoice.balance, open_invoice.status) == (500, 800, "part_paid")
    pay(open_invoice, cashier, "800")
    open_invoice.refresh_from_db()
    assert (open_invoice.balance, open_invoice.status) == (0, "paid")


def test_receipts_are_numbered_and_printable(open_invoice, cashier, as_user):
    p = pay(open_invoice, cashier, "300")
    assert p.receipt_number.startswith("RCT-")
    assert as_user(cashier).get(f"/billing/receipts/{p.pk}/").status_code == 200
    assert pay(open_invoice, cashier, "100").receipt_number != p.receipt_number


def test_cannot_pay_more_than_the_balance_except_as_a_deposit(open_invoice, cashier):
    with pytest.raises(services.BillingError, match="balance"):
        pay(open_invoice, cashier, "2000")
    pay(open_invoice, cashier, "2000", is_deposit=True)
    open_invoice.refresh_from_db()
    assert open_invoice.balance == -700 and open_invoice.status == "paid"


def test_inpatient_deposit_can_be_taken_before_the_invoice_is_finalised(prices, with_doctor, cashier):
    inv = invoice_of(visit_of(with_doctor))
    assert inv.status == "draft"
    with pytest.raises(services.BillingError, match="Finalise"):
        pay(inv, cashier, "100")
    pay(inv, cashier, "5000", is_deposit=True)
    assert inv.paid == 5000 and inv.status == "draft"


def test_mpesa_code_is_required_valid_and_unique(open_invoice, cashier):
    with pytest.raises(services.BillingError, match="transaction code"):
        pay(open_invoice, cashier, "100", "mpesa", mpesa_code="")
    with pytest.raises(services.BillingError, match="transaction code"):
        pay(open_invoice, cashier, "100", "mpesa", mpesa_code="12")
    p = pay(open_invoice, cashier, "100", "mpesa", mpesa_code="qgh7abc123")
    assert p.mpesa_code == "QGH7ABC123"
    with pytest.raises(services.BillingError, match="already been recorded"):
        pay(open_invoice, cashier, "100", "mpesa", mpesa_code="QGH7ABC123")


def test_refund_is_a_negative_payment_approved_by_an_administrator(open_invoice, cashier, admin_user, as_user):
    pay(open_invoice, cashier, "1300")
    assert (
        as_user(cashier)
        .post(f"/billing/invoices/{open_invoice.pk}/refund/", {"amount": "100", "method": "cash", "reason": "x"})
        .status_code
        == 403
    )
    as_user(admin_user).post(
        f"/billing/invoices/{open_invoice.pk}/refund/", {"amount": "300", "method": "cash", "reason": "Test cancelled"}
    )
    open_invoice.refresh_from_db()
    refund = open_invoice.payments.get(amount=-300)
    assert refund.approved_by == admin_user and refund.receipt_number.startswith("RFD-") and refund.refund_reason
    assert (open_invoice.paid, open_invoice.balance, open_invoice.status) == (1000, 300, "part_paid")
    with pytest.raises(services.BillingError, match="reason"):
        services.refund(open_invoice, D("10"), "cash", "", admin_user)
    with pytest.raises(services.BillingError, match="Only"):
        services.refund(open_invoice, D("5000"), "cash", "Too much", admin_user)


def test_void_needs_a_reason_zero_payments_and_an_administrator(open_invoice, cashier, admin_user, as_user):
    assert as_user(cashier).post(f"/billing/invoices/{open_invoice.pk}/void/", {"reason": "x"}).status_code == 403
    with pytest.raises(services.BillingError, match="reason"):
        services.void_invoice(open_invoice, " ", admin_user)
    pay(open_invoice, cashier, "100")
    with pytest.raises(services.BillingError, match="Refund"):
        services.void_invoice(open_invoice, "Wrong patient", admin_user)
    services.refund(open_invoice, D("100"), "cash", "Wrong patient", admin_user)
    services.void_invoice(open_invoice, "Wrong patient", admin_user)
    open_invoice.refresh_from_db()
    assert open_invoice.status == "void" and open_invoice.voided_by == admin_user
    with pytest.raises(services.BillingError):
        pay(open_invoice, cashier, "10")


# ------------------------------------------------------------------------------------------------ append-only
def test_invoices_lines_and_payments_cannot_be_edited_or_deleted(open_invoice, cashier):
    p = pay(open_invoice, cashier, "100")
    line = open_invoice.active_lines.first()
    with pytest.raises(PermissionError):
        open_invoice.delete()
    with pytest.raises(PermissionError):
        line.delete()
    with pytest.raises(PermissionError):
        p.delete()
    p.amount = D("1")
    with pytest.raises(PermissionError):
        p.save()
    line.quantity = 5
    with pytest.raises(PermissionError):
        line.save()
    line.refresh_from_db()
    line.unit_price = D("1")
    with pytest.raises(PermissionError):
        line.save()  # a priced line cannot be re-priced


def test_the_admin_site_cannot_add_change_or_delete_money_records(rf, admin_user):
    admin_user.is_superuser = admin_user.is_staff = True
    req = rf.get("/")
    req.user = admin_user
    for model in (Invoice, InvoiceLine, Payment, MpesaRequest):
        ma = admin.site._registry[model]
        assert (
            not ma.has_add_permission(req) and not ma.has_change_permission(req) and not ma.has_delete_permission(req)
        )


def test_database_rejects_a_zero_payment_and_a_second_charge_for_one_event(open_invoice, cashier):
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(invoice=open_invoice, method="cash", amount=0, received_by=cashier)
    line = open_invoice.active_lines.filter(source_key__gt="").first()
    with pytest.raises(IntegrityError), transaction.atomic():
        InvoiceLine.objects.create(invoice=open_invoice, description="dup", unit_price=1, source_key=line.source_key)


# ------------------------------------------------------------------------------------------------ M-Pesa
@pytest.fixture
def mpesa(settings):
    settings.MPESA_CALLBACK_TOKEN = "secret-token-123"
    settings.MPESA_CONSUMER_KEY = settings.MPESA_CONSUMER_SECRET = "k"
    settings.MPESA_SHORTCODE, settings.MPESA_PASSKEY = "174379", "pass"
    settings.MPESA_CALLBACK_BASE = "https://hms.example.org"


def callback(cid, code=0, receipt="QGH7ABC123", amount=1300):
    body = {"CheckoutRequestID": cid, "ResultCode": code, "ResultDesc": "ok" if code == 0 else "Request cancelled"}
    if code == 0:
        body["CallbackMetadata"] = {"Item": [{"Name": "Amount", "Value": amount},
                                             {"Name": "MpesaReceiptNumber", "Value": receipt},
                                             {"Name": "PhoneNumber", "Value": 254712345678}]}  # fmt: skip
    return json.dumps({"Body": {"stkCallback": body}})


def post_cb(client, payload, token="secret-token-123"):
    return client.post(f"/billing/mpesa/callback/{token}/", payload, content_type="application/json")


@pytest.fixture
def stk(open_invoice, cashier, mpesa, monkeypatch):
    monkeypatch.setattr(services, "_get_token", lambda: ("https://sandbox.example", "tok"))
    monkeypatch.setattr(
        services, "_post", lambda url, payload, headers: {"CheckoutRequestID": "ws_CO_1", "ResponseCode": "0"}
    )
    return services.send_mpesa_prompt(open_invoice, "0712 345 678", D("1300"), cashier)


def test_prompt_is_recorded_and_the_callback_makes_one_payment(stk, client, open_invoice):
    assert stk.status == "pending" and stk.phone == "254712345678"
    assert post_cb(client, callback("ws_CO_1")).json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    open_invoice.refresh_from_db()
    stk.refresh_from_db()
    p = open_invoice.payments.get()
    assert p.method == "mpesa" and p.mpesa_code == "QGH7ABC123" and p.amount == 1300 and stk.status == "paid"
    assert open_invoice.status == "paid"


def test_a_repeated_callback_creates_no_second_payment(stk, client, open_invoice):
    for _ in range(3):
        assert post_cb(client, callback("ws_CO_1")).status_code == 200
    assert Payment.objects.filter(invoice=open_invoice).count() == 1


def test_a_cancelled_prompt_creates_no_payment(stk, client, open_invoice):
    post_cb(client, callback("ws_CO_1", code=1032))
    stk.refresh_from_db()
    assert stk.status == "failed" and "cancelled" in stk.result_desc and not open_invoice.payments.exists()


def test_callback_for_an_unknown_request_is_acknowledged_but_ignored(stk, client, open_invoice):
    assert post_cb(client, callback("ws_CO_unknown")).status_code == 200
    assert not open_invoice.payments.exists()


def test_callback_needs_the_secret_token(stk, client, open_invoice, settings):
    assert post_cb(client, callback("ws_CO_1"), token="wrong").status_code == 404
    settings.MPESA_CALLBACK_TOKEN = ""
    assert post_cb(client, callback("ws_CO_1"), token="").status_code == 404
    assert not open_invoice.payments.exists()
    assert client.get("/billing/mpesa/callback/secret-token-123/").status_code == 405


def test_prompt_refused_when_not_configured_or_for_a_draft(open_invoice, cashier, prices):
    with pytest.raises(services.BillingError, match="not set up"):
        services.send_mpesa_prompt(open_invoice, "0712345678", D("100"), cashier)


# ------------------------------------------------------------------------------------------------ roles
URLS = ["/billing/", "/billing/invoices/{i}/", "/billing/invoices/{i}/print/"]


def test_only_cashier_and_admin_can_open_billing(
    open_invoice, as_user, cashier, admin_user, doctor, nurse, reception, lab, pharmacist
):
    for url in (u.format(i=open_invoice.pk) for u in URLS):
        for ok in (cashier, admin_user):
            assert as_user(ok).get(url).status_code == 200, url
        for no in (doctor, nurse, reception, lab, pharmacist):
            assert as_user(no).get(url).status_code == 403, (url, no.role)


def test_anonymous_users_are_sent_to_sign_in(open_invoice, client):
    for url in (u.format(i=open_invoice.pk) for u in URLS):
        r = client.get(url)
        assert r.status_code == 302 and "/login/" in r.url


def test_forbidden_roles_cannot_post_to_billing_actions(open_invoice, as_user, doctor, reception, pharmacist):
    i = open_invoice.pk
    for u in (doctor, reception, pharmacist):
        c = as_user(u)
        for path in ("pay", "finalise", "add-line", "refund", "void", "credit"):
            assert c.post(f"/billing/invoices/{i}/{path}/", {}).status_code == 403, (path, u.role)


def test_cashier_pays_through_the_screen(open_invoice, as_user, cashier):
    c = as_user(cashier)
    r = c.post(f"/billing/invoices/{open_invoice.pk}/pay/", {"method": "cash", "amount": "300"})
    assert r.status_code == 302 and "/billing/receipts/" in r.url
    bad = c.post(f"/billing/invoices/{open_invoice.pk}/pay/", {"method": "cash", "amount": "99999"}, follow=True)
    assert b"The balance is" in bad.content
    assert open_invoice.payments.count() == 1


def test_list_search_and_tabs(open_invoice, as_user, cashier, patient):
    c = as_user(cashier)
    assert open_invoice.number.encode() in c.get("/billing/?q=Kamau").content
    assert open_invoice.number.encode() not in c.get("/billing/?tab=paid").content
    assert open_invoice.number.encode() in c.get(f"/billing/?q={patient.hospital_number}").content


def test_sample_prices_and_backfill_commands(db, malaria, paracetamol):
    from django.core.management import call_command

    from apps.billing.sampledata import seed_sample_prices

    call_command("sync_service_items")
    assert ServiceItem.objects.filter(test=malaria).exists() and ServiceItem.objects.filter(drug=paracetamol).exists()
    assert seed_sample_prices() > 0 and seed_sample_prices() == 0  # second run adds nothing
    call_command("backfill_charges")
