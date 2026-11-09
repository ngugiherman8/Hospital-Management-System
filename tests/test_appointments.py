import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.appointments.models import Appointment, QueueEntry
from apps.appointments.tasks import mark_no_shows, send_due_reminders

pytestmark = pytest.mark.django_db


def book(patient, clinic, doctor, when):
    return Appointment.objects.create(patient=patient, clinic=clinic, doctor=doctor, starts_at=when)


def test_queue_tickets_increment_per_day(patient, clinic, db):
    from apps.patients.models import Patient

    other = Patient.objects.create(
        first_name="B", last_name="T", date_of_birth="2000-01-01", sex="M", phone="0711000002"
    )
    a, _ = QueueEntry.join(patient=patient, clinic=clinic)
    b, _ = QueueEntry.join(patient=other, clinic=clinic)
    assert (a.ticket, b.ticket) == ("A-001", "A-002")


def test_joining_twice_returns_same_entry(patient, clinic):
    a, created1 = QueueEntry.join(patient=patient, clinic=clinic)
    b, created2 = QueueEntry.join(patient=patient, clinic=clinic)
    assert a.pk == b.pk and created1 and not created2


def test_doctor_cannot_be_double_booked(patient, clinic, doctor, future):
    book(patient, clinic, doctor, future)
    with pytest.raises(IntegrityError), transaction.atomic():
        book(patient, clinic, doctor, future)


def test_cancelled_slot_can_be_rebooked(patient, clinic, doctor, future):
    first = book(patient, clinic, doctor, future)
    first.status = "cancelled"
    first.save()
    book(patient, clinic, doctor, future)


def test_booking_form_rejects_past_and_clash(login, reception, patient, clinic, doctor, future):
    c = login(reception)
    url = f"/appointments/new/{patient.pk}/"
    past = (timezone.localtime() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert b"future" in c.post(url, {"clinic": clinic.pk, "doctor": doctor.pk, "starts_at": past}).content
    when = timezone.localtime(future).strftime("%Y-%m-%dT%H:%M")
    assert c.post(url, {"clinic": clinic.pk, "doctor": doctor.pk, "starts_at": when}).status_code == 302
    assert b"already booked" in c.post(url, {"clinic": clinic.pk, "doctor": doctor.pk, "starts_at": when}).content


def test_reminders_sent_once(patient, clinic, doctor, future, caplog):
    book(patient, clinic, doctor, future)
    caplog.set_level("INFO")
    assert send_due_reminders() == 1
    assert send_due_reminders() == 0
    assert "SMS to 254712345678" in caplog.text


def test_reminder_not_sent_for_far_future_or_cancelled(patient, clinic, doctor):
    book(patient, clinic, doctor, timezone.now() + datetime.timedelta(days=5))
    assert send_due_reminders() == 0


def test_stale_booked_appointments_become_no_show(patient, clinic, doctor):
    a = book(patient, clinic, doctor, timezone.now() - datetime.timedelta(hours=14))
    assert mark_no_shows() == 1
    a.refresh_from_db()
    assert a.status == "no_show"


def test_arrived_adds_to_queue(login, reception, patient, clinic, doctor, future):
    a = book(patient, clinic, doctor, future)
    login(reception).post(f"/appointments/{a.pk}/arrived/")
    a.refresh_from_db()
    assert a.status == "arrived" and a.queue_entry.doctor == doctor and not a.queue_entry.is_walk_in


def test_queue_page_counts_and_filter(login, reception, patient, clinic):
    QueueEntry.join(patient=patient, clinic=clinic)
    c = login(reception)
    assert b"A-001" in c.get("/").content
    assert b"A-001" not in c.get("/?tab=lab").content
    assert b"A-001" in c.get("/?tab=triage").content


def test_health_endpoint(client, db):
    assert client.get("/health/").json() == {"status": "ok", "pending_migrations": []}
