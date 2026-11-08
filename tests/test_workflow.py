"""Each row of the status table (apps.appointments.models.TRANSITIONS), plus wrong-role and skipped-step cases."""

import pytest
from django.core.exceptions import PermissionDenied

from apps.appointments.models import QueueEntry, TransitionError
from apps.clinical.models import Visit, VisitDiagnosis
from apps.core.models import AuditLog
from apps.orders.models import Order
from tests.conftest import TRIAGE

pytestmark = pytest.mark.django_db


def stage(entry):
    entry.refresh_from_db()
    return entry.stage


def test_save_triage_moves_to_waiting_for_doctor(waiting, nurse, as_user):
    r = as_user(nurse).post(f"/clinical/triage/{waiting.pk}/", TRIAGE)
    assert r.status_code == 302 and stage(waiting) == "waiting_doctor"
    visit = Visit.objects.get(queue_entry=waiting)
    assert visit.triaged_by == nurse and visit.vitals.count() == 1


def test_save_triage_needs_a_saved_record_and_the_nurse_role(waiting, nurse, doctor):
    with pytest.raises(TransitionError):
        waiting.apply("save_triage", nurse)  # no triage record yet
    with pytest.raises(PermissionDenied):
        waiting.apply("save_triage", doctor)


def test_fast_track_needs_reason_and_is_audited(waiting, reception, nurse, as_user):
    c = as_user(reception)
    c.post(f"/queue/{waiting.pk}/emergency/", {"reason": ""})
    assert stage(waiting) == "waiting_triage"
    c.post(f"/queue/{waiting.pk}/emergency/", {"reason": "Collapsed, not breathing well"})
    assert stage(waiting) == "waiting_doctor"
    visit = Visit.objects.get(queue_entry=waiting)
    assert visit.priority == "emergency" and "Collapsed" in visit.fast_track_reason
    assert AuditLog.objects.filter(reason__startswith="Emergency fast-track").exists()
    assert as_user(nurse).post(f"/queue/{waiting.pk}/emergency/", {"reason": "x"}).status_code == 403


def test_nurse_completes_triage_after_fast_track(waiting, reception, nurse, as_user):
    as_user(reception).post(f"/queue/{waiting.pk}/emergency/", {"reason": "Seizure"})
    r = as_user(nurse).post(f"/clinical/triage/{waiting.pk}/", {**TRIAGE, "priority": "emergency"})
    assert r.status_code == 302
    assert Visit.objects.get(queue_entry=waiting).triaged_at is not None and stage(waiting) == "waiting_doctor"


def test_call_in_is_doctor_only(waiting, nurse, doctor, as_user):
    as_user(nurse).post(f"/clinical/triage/{waiting.pk}/", TRIAGE)
    assert as_user(nurse).post(f"/clinical/call-in/{waiting.pk}/").status_code == 403
    as_user(doctor).post(f"/clinical/call-in/{waiting.pk}/")
    assert stage(waiting) == "with_doctor"


def test_cannot_call_in_before_triage(waiting, doctor, as_user):
    as_user(doctor).post(f"/clinical/call-in/{waiting.pk}/")
    assert stage(waiting) == "waiting_triage"


def test_send_to_lab_needs_an_order(with_doctor, doctor, malaria):
    with pytest.raises(TransitionError):
        with_doctor.apply("send_to_lab", doctor)
    Order.objects.create(visit=Visit.objects.get(queue_entry=with_doctor), test=malaria)
    with_doctor.apply("send_to_lab", doctor)
    assert stage(with_doctor) == "at_lab"


def test_visit_cannot_leave_lab_while_a_test_has_no_result(with_doctor, doctor, lab, malaria, fbc, as_user):
    visit = Visit.objects.get(queue_entry=with_doctor)
    o1 = Order.objects.create(visit=visit, test=malaria)
    o2 = Order.objects.create(visit=visit, test=fbc)
    with_doctor.apply("send_to_lab", doctor)
    with pytest.raises(TransitionError):
        with_doctor.apply("lab_complete", lab)
    c = as_user(lab)
    c.post(f"/orders/lab/{o1.pk}/result/", {"value": "Positive", "flag": "abnormal"})
    assert stage(with_doctor) == "at_lab"  # one test still open
    c.post(f"/orders/lab/{o2.pk}/result/", {"value": "12.5", "unit": "g/dL", "flag": "normal"})
    assert stage(with_doctor) == "with_doctor"  # all results in: back to the doctor


def test_cancelling_the_last_open_test_returns_patient_to_doctor(with_doctor, doctor, malaria, as_user):
    visit = Visit.objects.get(queue_entry=with_doctor)
    o = Order.objects.create(visit=visit, test=malaria)
    as_user(doctor).post(f"/clinical/visits/{visit.pk}/send-to-lab/")
    assert stage(with_doctor) == "at_lab"
    as_user(doctor).post(f"/orders/visits/{visit.pk}/{o.pk}/cancel/")
    assert stage(with_doctor) == "with_doctor"


def test_complete_needs_diagnosis_and_home_or_refer(with_doctor, doctor, icd):
    visit = Visit.objects.get(queue_entry=with_doctor)
    with pytest.raises(TransitionError):
        with_doctor.apply("complete", doctor)  # no diagnosis
    VisitDiagnosis.objects.create(visit=visit, icd10=icd, added_by=doctor)
    with pytest.raises(TransitionError):
        with_doctor.apply("complete", doctor)  # no outcome
    visit.outcome = "refer"
    visit.save()
    with pytest.raises(TransitionError):
        with_doctor.apply("complete", doctor)  # refer without a facility
    visit.referred_to = "County referral hospital"
    visit.save()
    with_doctor.apply("complete", doctor)
    assert stage(with_doctor) == "done"


def test_wrong_role_and_skipped_steps_are_refused(waiting, nurse, doctor):
    with pytest.raises(TransitionError):
        waiting.apply("complete", doctor)  # skips triage, call-in and consultation
    with pytest.raises(PermissionDenied):
        waiting.apply("complete", nurse)
    with pytest.raises(TransitionError):
        waiting.apply("discharge", doctor)


def test_admit_needs_an_admission_and_discharge_needs_a_summary(with_doctor, doctor, icd, beds, as_user):
    visit = Visit.objects.get(queue_entry=with_doctor)
    with pytest.raises(TransitionError):
        with_doctor.apply("admit", doctor)  # no admission yet
    VisitDiagnosis.objects.create(visit=visit, icd10=icd, added_by=doctor)
    c = as_user(doctor)
    c.post(f"/wards/admit/visit/{visit.pk}/", {"bed": beds[0].pk, "reason": "Severe malaria"})
    assert stage(with_doctor) == "admitted"
    with pytest.raises(TransitionError):
        with_doctor.apply("discharge", doctor)  # not discharged yet
    c.post(
        f"/wards/visits/{visit.pk}/discharge/",
        {"condition_at_discharge": "improved", "summary": "Treated", "follow_up": "Review in 1 week"},
    )
    assert stage(with_doctor) == "done"


def test_queue_orders_emergency_then_urgent_then_routine(clinic, db, reception, as_user):
    from apps.patients.models import Patient

    entries = []
    for i, prio in enumerate(["routine", "urgent", "emergency", "routine"]):
        p = Patient.objects.create(first_name=f"P{i}", last_name="Test", date_of_birth="2000-01-01", sex="F",
                                   phone=f"07110000{i:02d}")  # fmt: skip
        e, _ = QueueEntry.join(patient=p, clinic=clinic)
        Visit.objects.create(patient=p, queue_entry=e, priority=prio)
        entries.append(e)
    body = as_user(reception).get("/").content.decode()
    order = [body.index(e.ticket) for e in (entries[2], entries[1], entries[0], entries[3])]
    assert order == sorted(order)
