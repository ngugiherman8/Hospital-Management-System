import datetime

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role
from apps.billing.models import InvoiceLine, ItemPrice, ServiceItem
from apps.hr import services as hr
from apps.hr.models import Department, LeaveType, Licence, StaffProfile
from apps.patients.models import Patient
from apps.theatre import services
from apps.theatre.models import Procedure, Theatre, TheatreCase
from conftest import make_user


@pytest.fixture
def world(db, patient):
    svc = ServiceItem.objects.create(code="THEATRE-APP", name="Appendicectomy", category="other")
    ItemPrice.objects.create(service=svc, payer_kind="cash", amount=40000)
    return {
        "theatre": Theatre.objects.create(name="Theatre 1"),
        "other": Theatre.objects.create(name="Theatre 2"),
        "proc": Procedure.objects.create(name="Appendicectomy", default_minutes=60, service=svc),
        "free": Procedure.objects.create(name="Minor wound repair", default_minutes=30),
        "surgeon": make_user(Role.DOCTOR, username="surgeon"),
        "anaes": make_user(Role.DOCTOR, username="anaes"),
        "nurse": make_user(Role.NURSE, username="scrub"),
        "patient": patient,
        "patient2": Patient.objects.create(
            first_name="Otieno", last_name="Odhiambo", date_of_birth=datetime.date(1980, 1, 1), sex="M"
        ),
    }


def at(hours=48, minute=0):
    base = timezone.localtime(timezone.now() + datetime.timedelta(hours=hours))
    return base.replace(minute=minute, second=0, microsecond=0)


def book(w, start=None, theatre=None, minutes=60, urgency="elective", patient=None, **team):
    team.setdefault("surgeon", w["surgeon"])
    return services.book(
        patient=patient or w["patient"], theatre=theatre or w["theatre"], procedure=w["proc"], start=start or at(),
        minutes=minutes, urgency=urgency, user=w["surgeon"], **team,
    )  # fmt: skip


def test_booking_gets_a_number(world):
    case, _ = book(world)
    assert case.number.startswith("TC-") and case.status == "booked"


def test_same_theatre_cannot_be_double_booked_even_during_cleaning(world):
    book(world)  # 60 min from `at()`
    with pytest.raises(ValidationError, match="busy"):
        book(world, start=at() + datetime.timedelta(minutes=70), surgeon=world["anaes"], patient=world["patient2"])
    book(world, start=at() + datetime.timedelta(minutes=75), surgeon=world["anaes"], patient=world["patient2"])
    book(world, theatre=world["other"], surgeon=world["anaes"], patient=world["patient2"])  # another theatre is fine


def test_a_surgeon_cannot_be_in_two_theatres(world):
    book(world)
    with pytest.raises(ValidationError, match="already in"):
        book(world, theatre=world["other"])


def test_a_patient_cannot_be_booked_twice_at_once(world):
    book(world)
    with pytest.raises(ValidationError, match="This patient"):
        book(world, theatre=world["other"], surgeon=world["anaes"])


def test_leave_and_licence_block_the_team(world):
    dept = Department.objects.create(name="Theatre")
    prof = StaffProfile.objects.create(
        first_name="S", last_name="Urgeon", department=dept, job_title="Surgeon", cadre="medical",
        date_joined=datetime.date(2020, 1, 1), user=world["surgeon"],
    )  # fmt: skip
    annual = LeaveType.objects.create(name="Annual", annual_days=21)
    hr.adjust_leave(prof, annual, 10, "x", world["anaes"])
    start = at()
    if start.weekday() == 6:  # leave does not count Sundays
        start = at(72)
    day = start.date()
    req = hr.request_leave(prof, annual, day, day, "", world["anaes"])
    hr.decide_leave(req, True, make_user(Role.HR, username="hr"))
    with pytest.raises(ValidationError, match="approved leave"):
        book(world, start=start)
    Licence.objects.create(staff=prof, body="kmpdc", number="1", expires_on=day - datetime.timedelta(days=1))
    with pytest.raises(ValidationError, match="expired"):
        book(world, start=at(hours=24 * 20))


def test_emergencies_are_booked_anyway_with_a_note(world):
    book(world)
    case, warnings = book(world, urgency="emergency")
    assert warnings and "Emergency booked despite" in case.notes


def test_planned_work_cannot_be_booked_in_the_past(world):
    with pytest.raises(ValidationError, match="past"):
        book(world, start=at(hours=-5))


def test_start_needs_the_checklist_unless_emergency_with_a_reason(world):
    case, _ = book(world)
    with pytest.raises(ValidationError, match="Consent"):
        services.start(case, world["surgeon"])
    services.update_checklist(case, consent_signed=True, identity_checked=True)
    assert services.start(case, world["surgeon"]).status == "in_theatre"
    emergency, _ = book(world, urgency="emergency", theatre=world["other"], surgeon=world["anaes"], start=at(5))
    with pytest.raises(ValidationError):
        services.start(emergency, world["anaes"])
    with pytest.raises(ValidationError):
        services.start(emergency, world["anaes"], " ")
    done = services.start(emergency, world["anaes"], "Bleeding, no time")
    assert done.override_reason == "Bleeding, no time"


def test_one_case_at_a_time_in_a_theatre(world):
    a, _ = book(world)
    b, _ = book(world, start=at(60), surgeon=world["anaes"])
    for c in (a, b):
        services.update_checklist(c, consent_signed=True, identity_checked=True)
    services.start(a, world["surgeon"])
    with pytest.raises(ValidationError, match="still in use"):
        services.start(b, world["surgeon"])


def test_finishing_charges_the_bill_once(world):
    case, _ = book(world)
    services.update_checklist(case, consent_signed=True, identity_checked=True)
    services.start(case, world["surgeon"])
    case, charged = services.complete(case, world["surgeon"])
    assert charged is not None and charged.unit_price == 40000
    assert InvoiceLine.objects.filter(source_key=f"theatre:{case.pk}").count() == 1
    with pytest.raises(ValidationError):
        services.complete(case, world["surgeon"])


def test_no_price_means_no_charge_but_no_failure(world):
    case, _ = services.book(
        patient=world["patient"], theatre=world["theatre"], procedure=world["free"], start=at(), minutes=30,
        urgency="elective", user=world["surgeon"], surgeon=world["surgeon"],
    )  # fmt: skip
    services.update_checklist(case, consent_signed=True, identity_checked=True)
    services.start(case, world["surgeon"])
    case, charged = services.complete(case, world["surgeon"])
    assert charged is None and case.status == "completed"


def test_cancel_postpone_and_move(world):
    case, _ = book(world)
    with pytest.raises(ValidationError, match="reason"):
        services.close_without_surgery(case, "cancelled", " ")
    other, _ = book(world, start=at(60), surgeon=world["anaes"])
    with pytest.raises(ValidationError, match="busy"):
        services.reschedule(other, world["theatre"], at(), 60, world["surgeon"])
    services.reschedule(other, world["theatre"], at(80), 60, world["surgeon"])
    assert services.close_without_surgery(case, "postponed", "No blood available").status == "postponed"
    book(world, surgeon=world["surgeon"])  # the freed slot can be used again


def test_utilisation(world):
    case, _ = book(world)
    services.update_checklist(case, consent_signed=True, identity_checked=True)
    services.start(case, world["surgeon"])
    services.complete(case, world["surgeon"])
    rows = services.utilisation(timezone.localdate(), timezone.localdate())
    assert rows[0]["cases"] == 1


def test_pages_and_roles(world):
    boss, doc, nurse, cashier = Client(), Client(), Client(), Client()
    boss.force_login(make_user(Role.MANAGER, username="boss"))
    doc.force_login(world["surgeon"])
    nurse.force_login(world["nurse"])
    cashier.force_login(make_user(Role.CASHIER, username="cash"))
    case, _ = book(world)
    assert cashier.get("/theatre/").status_code == 403
    assert boss.get("/theatre/").status_code == 200
    assert boss.get("/theatre/new/").status_code == 403  # managers watch, they do not book
    assert boss.post(f"/theatre/cases/{case.pk}/start/").status_code == 403
    assert doc.get(f"/theatre/cases/{case.pk}/").status_code == 200
    assert doc.get("/theatre/setup/").status_code == 403
    assert boss.get("/theatre/setup/").status_code == 200
    r = nurse.post(f"/theatre/cases/{case.pk}/checklist/", {"consent_signed": "on", "identity_checked": "on"})
    assert r.status_code == 302 and TheatreCase.objects.get().checklist_ready
    nurse.post(f"/theatre/cases/{case.pk}/start/")
    assert TheatreCase.objects.get().status == "in_theatre"
    form = {
        "patient_number": world["patient"].hospital_number, "theatre": world["other"].pk, "procedure": world["proc"].pk,
        "urgency": "elective", "scheduled_start": at(100).strftime("%Y-%m-%dT%H:%M"), "surgeon": world["surgeon"].pk,
    }  # fmt: skip
    assert doc.post("/theatre/new/", form).status_code == 302
    assert TheatreCase.objects.count() == 2
    assert doc.post("/theatre/new/", {**form, "patient_number": "NOPE"}).status_code == 200
