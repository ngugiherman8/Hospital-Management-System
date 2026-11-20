import datetime
import json
from decimal import Decimal as D

import pytest
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.billing import services as billing
from apps.billing.models import ServiceItem
from apps.patients.models import Patient
from apps.sync import capture, receiver, sender
from apps.sync.models import AppliedChange, Outbox


def tracked_models():
    return [m for m in apps.get_models() if issubclass(m, tuple(capture.TRACKED_BASES))]


def everything():
    """Every row of every synchronised table, as JSON-safe dicts."""
    out = {}
    for model in tracked_models():
        out[model._meta.label] = {str(o.pk): capture.snapshot(o) for o in model._base_manager.all()}
    return out


def wipe_synchronised_tables():
    """Make this database look like a brand new cloud copy (the outbox itself is kept)."""
    tables = ", ".join(f'"{m._meta.db_table}"' for m in tracked_models())
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(f"TRUNCATE {tables} CASCADE")
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    assert not Patient.objects.exists()


@pytest.fixture
def busy_day(db, patient, cashier, doctor, catalogue):
    """A day of ordinary activity: a visit, an invoice, payments, a refund, stock."""
    from django.core.management import call_command

    from apps.pharmacy_rx.models import Drug
    from apps.stock.models import GoodsReceipt, Supplier
    from apps.stock.services import receive_stock

    call_command("seed_billing", verbosity=0)
    visit = Appointment.objects.create(patient=patient, kind="walk_in")
    inv = billing.draft_invoice_for(patient, visit)
    billing.add_line(inv, ServiceItem.objects.get(code="CONS-GENERAL"), 1, cashier)
    inv = billing.finalise(inv, cashier)
    billing.take_payment(inv, "cash", D("300"), cashier)
    billing.take_payment(inv, "mpesa", D("300"), cashier, mpesa_code="SYNC1")
    receipt = GoodsReceipt.objects.create(supplier=Supplier.objects.create(name="S"), received_by=cashier)
    receive_stock(
        receipt, Drug.objects.first(), "B1", timezone.localdate() + datetime.timedelta(days=300), 50, 1, cashier
    )
    patient.phone = "0700000000"
    patient.save()
    return inv


# ---------- capture ----------


def test_every_save_writes_an_outbox_row_with_rising_versions(db, patient):
    def rows():
        return Outbox.objects.filter(model_label="patients.Patient", object_id=str(patient.pk))

    assert [r.version for r in rows()] == [1]
    patient.phone = "+254799999999"
    patient.save()
    assert [r.version for r in rows()] == [1, 2]
    assert rows().last().data["phone"] == "+254799999999"


def test_the_outbox_row_belongs_to_the_same_transaction(db, patient):
    before = Outbox.objects.count()
    with pytest.raises(RuntimeError), transaction.atomic():
        patient.phone = "0711111111"
        patient.save()
        raise RuntimeError("the save is rolled back")
    assert Outbox.objects.count() == before


def test_passwords_never_leave_the_server(db, doctor):
    row = Outbox.objects.filter(model_label="accounts.User", object_id=str(doctor.pk)).last()
    assert "password" not in row.data and "last_login" not in row.data


def test_money_and_stock_rows_are_captured_too(busy_day):
    labels = set(Outbox.objects.values_list("model_label", flat=True))
    assert {"billing.Payment", "billing.Invoice", "stock.StockMovement", "core.AuditLog"} <= labels


def test_outbox_rows_cannot_be_deleted(busy_day):
    with pytest.raises(PermissionError):
        Outbox.objects.first().delete()


# ---------- the cloud applies each change once ----------


@pytest.mark.django_db(transaction=True)
def test_cloud_matches_local_after_the_internet_was_cut(busy_day, settings):
    """Work all day with the cloud unreachable, then reconnect: the cloud ends up identical."""
    reference = everything()
    queued = Outbox.objects.filter(sent_at__isnull=True).count()
    assert queued > 50

    def cut(payload):
        raise OSError("no route to host")

    t0 = timezone.now()
    for attempt in range(3):  # three failed attempts, each waiting longer than the last
        result = sender.run_once(post=cut, now=t0 + datetime.timedelta(hours=attempt * 2))
        assert result["sent"] == 0 and "no route" in result["error"]
    head = Outbox.objects.filter(sent_at__isnull=True).first()
    assert head.attempts == 3 and head.next_attempt_at > t0
    assert Outbox.objects.filter(sent_at__isnull=True).count() == queued  # nothing lost, nothing sent

    wipe_synchronised_tables()  # the cloud copy knows nothing yet

    def reconnect(payload):
        acked, error = receiver.apply_batch(payload["changes"])
        return {"acked": acked, "error": error}

    later = t0 + datetime.timedelta(days=1)
    total = 0
    while True:
        r = sender.run_once(post=reconnect, now=later, batch=7)
        assert r["error"] is None
        if not r["sent"]:
            break
        total += r["sent"]
    assert total == queued
    assert Outbox.objects.filter(sent_at__isnull=True).count() == 0
    assert everything() == reference


@pytest.mark.django_db(transaction=True)
def test_a_repeated_change_is_applied_once(busy_day):
    reference = everything()
    changes = list(Outbox.objects.all())
    wipe_synchronised_tables()
    payload = [
        {
            "seq": r.id,
            "model": r.model_label,
            "object_id": r.object_id,
            "version": r.version,
            "action": r.action,
            "data": r.data,
        }
        for r in changes
    ]
    acked, error = receiver.apply_batch(payload)
    assert error is None and len(acked) == len(payload)
    again, error = receiver.apply_batch(payload)  # the whole batch arrives a second time
    assert error is None and len(again) == len(payload)
    assert AppliedChange.objects.count() == len(payload)
    assert everything() == reference


def test_an_older_version_arriving_late_does_not_overwrite_a_newer_one(db, patient):
    rows = list(Outbox.objects.filter(model_label="patients.Patient", object_id=str(patient.pk)))
    patient.phone = "+254722222222"
    patient.save()
    v1, v2 = rows[0], Outbox.objects.filter(model_label="patients.Patient", object_id=str(patient.pk)).last()
    AppliedChange.objects.all().delete()
    assert receiver.apply_change("patients.Patient", str(patient.pk), 2, "save", v2.data) == "applied"
    assert receiver.apply_change("patients.Patient", str(patient.pk), 1, "save", v1.data) == "stale"
    patient.refresh_from_db()
    assert patient.phone == "+254722222222"


def test_deletes_are_applied(db, patient):
    from apps.dental.models import DentalProcedure

    proc = DentalProcedure.objects.create(code="ZZ", name="Test")
    proc_id = proc.pk
    proc.delete()
    last = Outbox.objects.filter(model_label="dental.DentalProcedure", object_id=str(proc_id)).last()
    assert last.action == "delete"
    now = timezone.now()
    DentalProcedure(pk=proc_id, code="ZZ", name="Test", created_at=now, updated_at=now).save_base(
        raw=True
    )  # the cloud still has it
    receiver.apply_change(last.model_label, last.object_id, last.version, "delete", None)
    assert not DentalProcedure.objects.filter(pk=proc_id).exists()


def test_unknown_models_and_the_sync_tables_are_refused(db):
    with pytest.raises(receiver.BadChange):
        receiver.apply_change("nothing.Here", "1", 1, "save", {})
    with pytest.raises(receiver.BadChange):
        receiver.apply_change("sync.Outbox", "1", 1, "save", {})


def test_a_bad_change_stops_the_batch_and_keeps_order(db, patient):
    good = {"seq": 1, "model": "patients.Patient", "object_id": "999999", "version": 1, "action": "save", "data": None}
    acked, error = receiver.apply_batch([good])
    assert acked == [] and "needs data" in error


def test_retry_waits_longer_each_time():
    waits = [sender.backoff(n) for n in range(1, 20)]
    assert waits == sorted(waits) and waits[0] == datetime.timedelta(seconds=10) and waits[-1] == sender.MAX_WAIT


def test_sender_respects_the_retry_wait(busy_day):
    calls = []

    def cut(payload):
        calls.append(1)
        raise OSError("down")

    now = timezone.now()
    sender.run_once(post=cut, now=now)
    sender.run_once(post=cut, now=now + datetime.timedelta(seconds=1))  # too soon: not even tried
    assert len(calls) == 1


# ---------- the endpoints ----------


def test_receive_endpoint_is_off_unless_this_is_the_cloud(client, settings):
    settings.SYNC_TOKEN = "tok"
    assert client.post("/sync/receive/", "{}", content_type="application/json").status_code == 404  # local mode


def test_receive_endpoint_checks_the_token_and_applies(client, settings, patient):
    settings.HMS_MODE, settings.SYNC_TOKEN = "cloud", "tok"
    row = Outbox.objects.filter(model_label="patients.Patient").first()
    AppliedChange.objects.all().delete()
    body = json.dumps({"changes": [{"seq": row.id, "model": row.model_label, "object_id": row.object_id,
                                    "version": row.version, "action": "save", "data": row.data}]})  # fmt: skip
    assert client.post("/sync/receive/", body, content_type="application/json").status_code == 403
    r = client.post("/sync/receive/", body, content_type="application/json", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200 and r.json()["acked"] == [row.id]
    assert client.post("/sync/receive/", "nonsense", content_type="application/json",
                       headers={"Authorization": "Bearer tok"}).status_code == 400  # fmt: skip


def test_cloud_copy_cannot_be_changed_through_the_website(login, settings, doctor, cashier, patient, admin_user):
    settings.HMS_MODE = "cloud"
    c = login(admin_user)
    assert c.get("/patients/").status_code == 200  # reading is fine
    r = c.post(f"/patients/{patient.pk}/edit/", {"first_name": "X"})
    assert r.status_code == 403
    patient.refresh_from_db()
    assert patient.first_name != "X"
    assert c.post("/logout/").status_code == 302  # signing out still works


def test_sync_status_page_is_for_administrators(login, admin_user, doctor, busy_day):
    assert login(admin_user).get("/sync/status/").status_code == 200
    assert login(doctor).get("/sync/status/").status_code == 403


def test_queue_status_reports_number_and_age(busy_day):
    q = sender.queue_status(now=timezone.now() + datetime.timedelta(minutes=10))
    assert q["unsent"] > 0 and q["oldest_age_seconds"] >= 600


@pytest.mark.django_db(transaction=True)
def test_a_record_is_queued_before_what_is_created_from_it(db, patient, catalogue):
    """Registering a walk-in makes an invoice as the appointment is saved. The invoice must not be queued first, or
    the cloud would be asked to store an invoice for an appointment it has not seen."""
    from django.core.management import call_command

    call_command("seed_billing", verbosity=0)
    visit = Appointment.objects.create(patient=patient, kind="walk_in")
    ids = {
        label: Outbox.objects.filter(model_label=label, object_id=str(pk)).first().id
        for label, pk in (("appointments.Appointment", visit.pk), ("billing.Invoice", visit.invoices.get().pk))
    }
    assert ids["appointments.Appointment"] < ids["billing.Invoice"]
    reference = everything()
    wipe_synchronised_tables()

    def apply_here(payload):
        acked, error = receiver.apply_batch(payload["changes"])
        return {"acked": acked, "error": error}

    while sender.run_once(post=apply_here, batch=1)["sent"]:  # even one row at a time, the cloud accepts every row
        pass
    assert everything() == reference


@pytest.mark.django_db(transaction=True)
def test_cloud_made_ids_cannot_clash_with_copied_ones(db, patient, catalogue, settings):
    """On the cloud, an audit entry written after rows were copied must not take an id the hospital will send."""
    from django.core.management import call_command

    from apps.core.models import AuditLog

    settings.HMS_MODE = "cloud"
    call_command("prepare_cloud", verbosity=0)
    call_command("prepare_cloud", verbosity=0)  # safe to repeat
    mine = AuditLog.objects.create(action="view", model_label="x", object_id="1", object_repr="cloud sign-in")
    assert mine.pk >= 1_000_000_000
    old = AuditLog.objects.filter(pk__lt=1_000_000_000).first()
    assert old is not None  # hospital rows keep their own small ids
    settings.HMS_MODE = "local"
    with pytest.raises(Exception, match="cloud"):
        call_command("prepare_cloud", verbosity=0)
