import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.appointments.models import Appointment, Status, TransitionBlocked
from apps.clinical.models import Consultation, Diagnosis, TriageRecord


def walk_in(patient, **kw):
    return Appointment.objects.create(patient=patient, kind="walk_in", **kw)


def test_walk_ins_get_daily_queue_numbers(patient):
    first = walk_in(patient)
    second = walk_in(patient)
    assert (first.queue_label, second.queue_label) == ("A-001", "A-002")
    assert first.status == Status.WAITING_TRIAGE


def test_booked_appointment_has_no_queue_number_until_check_in(patient, reception, tomorrow_10am):
    visit = Appointment(patient=patient, kind="booked", scheduled_for=tomorrow_10am)
    visit.full_clean()
    visit.save()
    assert visit.status == Status.BOOKED
    assert visit.queue_number is None
    visit.move_to(Status.WAITING_TRIAGE, reception)
    assert visit.queue_number == 1
    assert visit.visit_date == timezone.localdate()


def test_booked_needs_a_time(patient):
    with pytest.raises(ValidationError):
        Appointment(patient=patient, kind="booked").full_clean()


def test_past_time_is_rejected(patient):
    past = timezone.now() - datetime.timedelta(hours=2)
    with pytest.raises(ValidationError):
        Appointment(patient=patient, kind="booked", scheduled_for=past).full_clean()


def test_doctor_cannot_be_double_booked(patient, doctor, tomorrow_10am):
    Appointment.objects.create(patient=patient, kind="booked", scheduled_for=tomorrow_10am, doctor=doctor)
    with pytest.raises(ValidationError):
        Appointment(patient=patient, kind="booked", scheduled_for=tomorrow_10am, doctor=doctor).full_clean()


def test_cancelled_slot_can_be_rebooked(patient, doctor, reception, tomorrow_10am):
    first = Appointment.objects.create(patient=patient, kind="booked", scheduled_for=tomorrow_10am, doctor=doctor)
    first.move_to(Status.CANCELLED, reception)
    Appointment(patient=patient, kind="booked", scheduled_for=tomorrow_10am, doctor=doctor).full_clean()


def test_status_moves_follow_the_workflow_and_roles(patient, reception, nurse, doctor):
    visit = walk_in(patient)
    with pytest.raises(PermissionError):
        visit.move_to(Status.WITH_DOCTOR, doctor)  # cannot skip triage
    with pytest.raises(PermissionError):
        visit.move_to(Status.WAITING_DOCTOR, reception)  # wrong role
    with pytest.raises(TransitionBlocked):
        visit.move_to(Status.WAITING_DOCTOR, nurse)  # triage not saved yet
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Fever")
    visit.move_to(Status.WAITING_DOCTOR, nurse)
    visit.move_to(Status.WITH_DOCTOR, doctor)
    with pytest.raises(TransitionBlocked):
        visit.move_to(Status.COMPLETED, doctor)  # no diagnosis or outcome yet
    consultation = Consultation.objects.create(appointment=visit, doctor=doctor, outcome="home")
    Diagnosis.objects.create(consultation=consultation, icd10_code="B54", name="Malaria")
    visit.refresh_from_db()
    visit.move_to(Status.COMPLETED, doctor)
    assert visit.allowed_next(doctor) == []  # finished visits are closed


def test_queue_page_lists_todays_visits(login, reception, patient):
    walk_in(patient)
    response = login(reception).get("/appointments/")
    assert response.status_code == 200
    assert b"Wanjiru Kamau" in response.content
    assert b"A-001" in response.content


def test_status_endpoint_enforces_roles(login, reception, nurse, patient):
    visit = walk_in(patient)
    response = login(reception).post(f"/appointments/{visit.pk}/status/", {"status": "waiting_doctor"})
    assert response.status_code == 403
    response = login(nurse).post(f"/appointments/{visit.pk}/status/", {"status": "waiting_doctor"})
    assert response.status_code == 302
    visit.refresh_from_db()
    assert visit.status == Status.WAITING_TRIAGE  # blocked: no triage record
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Cough")
    login(nurse).post(f"/appointments/{visit.pk}/status/", {"status": "waiting_doctor"})
    visit.refresh_from_db()
    assert visit.status == Status.WAITING_DOCTOR


def test_status_endpoint_rejects_get(login, nurse, patient):
    visit = walk_in(patient)
    assert login(nurse).get(f"/appointments/{visit.pk}/status/").status_code == 405


def test_reception_books_through_the_form(login, reception, patient, tomorrow_10am):
    response = login(reception).post(
        f"/appointments/new/{patient.pk}/",
        {"kind": "booked", "clinic": "general", "scheduled_for": tomorrow_10am.astimezone().strftime("%Y-%m-%dT%H:%M")},
    )
    assert response.status_code == 302
    assert Appointment.objects.filter(patient=patient, status=Status.BOOKED).count() == 1
