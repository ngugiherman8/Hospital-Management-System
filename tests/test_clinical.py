import threading
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client

from apps.clinical.models import ClinicalNote, Visit, VisitDiagnosis, VitalSigns
from apps.core.models import AuditLog, Sequence
from apps.orders.models import Order, Result
from apps.pharmacy_rx.models import Prescription
from apps.wards.models import Admission, Bed, BedMove
from tests.conftest import TRIAGE

pytestmark = pytest.mark.django_db


def visit_of(entry):
    return Visit.objects.get(queue_entry=entry)


# ------------------------------------------------------------------------------------------------ triage
def test_out_of_range_vitals_are_flagged():
    v = VitalSigns(
        temperature=Decimal("38.6"), pulse=120, spo2=88, bp_systolic=150, bp_diastolic=95, blood_sugar=Decimal("2.5")
    )
    flags = dict(v.alerts)
    assert flags["Temperature"] == "high" and flags["Pulse"] == "high" and flags["Oxygen saturation"] == "critical"
    assert flags["Blood pressure"] == "high" and flags["Blood sugar"] == "low"
    assert VitalSigns(temperature=Decimal("36.8"), pulse=80, spo2=98).alerts == []


def test_bmi_is_calculated():
    assert VitalSigns(weight_kg=Decimal("64"), height_cm=Decimal("165")).bmi == Decimal("23.5")


def test_triage_page_flags_high_temperature_and_rejects_impossible_values(waiting, nurse, as_user):
    c = as_user(nurse)
    bad = c.post(f"/clinical/triage/{waiting.pk}/", {**TRIAGE, "temperature": "60"})
    assert bad.status_code == 200 and b"between 25 and 45" in bad.content
    waiting.refresh_from_db()
    assert waiting.stage == "waiting_triage"
    c.post(f"/clinical/triage/{waiting.pk}/", TRIAGE)
    page = as_user(nurse).get(f"/clinical/triage/{waiting.pk}/")
    assert page.status_code == 200  # waiting_doctor: the nurse may still correct it


def test_triage_needs_a_complaint(waiting, nurse, as_user):
    r = as_user(nurse).post(f"/clinical/triage/{waiting.pk}/", {**TRIAGE, "complaint": ""})
    assert r.status_code == 200 and not Visit.objects.filter(queue_entry=waiting, triaged_at__isnull=False).exists()


def test_doctor_and_reception_can_view_triage_read_only(waiting, nurse, doctor, reception, as_user):
    as_user(nurse).post(f"/clinical/triage/{waiting.pk}/", TRIAGE)
    for u in (doctor, reception):
        r = as_user(u).get(f"/clinical/triage/{waiting.pk}/")
        assert r.status_code == 200 and b"View only" in r.content


# ------------------------------------------------------------------------------------------------ notes
def test_saving_notes_twice_keeps_version_one_readable_and_both_in_the_audit_log(with_doctor, doctor, as_user):
    c = as_user(doctor)
    url = f"/clinical/consult/{with_doctor.pk}/"
    c.post(url, {"history": "Chills at night.", "action": "save"})
    c.post(url, {"history": "Chills at night. Travelled upcountry.", "action": "save"})
    visit = visit_of(with_doctor)
    current = visit.current_note("history")
    assert current.version == 2 and current.text.endswith("upcountry.")
    assert [n.text for n in current.history] == ["Chills at night."]
    assert ClinicalNote.objects.filter(visit=visit, kind="history").count() == 2
    assert AuditLog.objects.filter(action="create", object_repr__startswith="History").count() == 2
    assert b"Earlier versions" in c.get(url).content


def test_notes_cannot_be_edited_or_deleted(with_doctor, doctor):
    n = ClinicalNote.objects.create(visit=visit_of(with_doctor), kind="progress", text="a", author=doctor)
    n.text = "b"
    with pytest.raises(PermissionError):
        n.save()
    with pytest.raises(PermissionError):
        n.delete()


def test_progress_note_amendment_needs_a_reason_and_keeps_the_original(with_doctor, doctor, nurse, as_user):
    visit = visit_of(with_doctor)
    note = ClinicalNote.objects.create(visit=visit, kind="progress", text="Fever settling", author=nurse)
    c = as_user(nurse)
    c.post(f"/clinical/visits/{visit.pk}/notes/{note.pk}/amend/", {"text": "Fever settled", "reason": ""})
    assert ClinicalNote.objects.filter(visit=visit).count() == 1
    c.post(f"/clinical/visits/{visit.pk}/notes/{note.pk}/amend/", {"text": "Fever settled", "reason": "Typo"})
    assert ClinicalNote.objects.filter(visit=visit).count() == 2
    latest = ClinicalNote.current_for(visit, "progress").get()
    assert latest.text == "Fever settled" and latest.history[0].text == "Fever settling"


def test_nurse_can_open_consultation_but_not_change_it(with_doctor, nurse, as_user):
    c = as_user(nurse)
    r = c.get(f"/clinical/consult/{with_doctor.pk}/")
    assert r.status_code == 200 and b"View only" in r.content and b"Finish and send" not in r.content


# ------------------------------------------------------------------------------------------------ orders
def test_order_numbers_are_gap_free_and_per_kind(with_doctor, malaria, db):
    visit = visit_of(with_doctor)
    a = Order.objects.create(visit=visit, test=malaria)
    b = Order.objects.create(visit=visit, test=malaria)
    year = a.ordered_at.year
    assert a.number == f"LAB-{year}-000001" and b.number == f"LAB-{year}-000002"
    assert Sequence.next(f"LAB-{year}") == 3


def test_doctor_orders_lab_enters_result_doctor_sees_it(with_doctor, doctor, lab, malaria, as_user):
    visit = visit_of(with_doctor)
    d = as_user(doctor)
    d.post(f"/orders/visits/{visit.pk}/add/", {"test": malaria.pk, "clinical_notes": "Fever 3 days"})
    order = Order.objects.get(visit=visit)
    d.post(f"/clinical/visits/{visit.pk}/send-to-lab/")
    as_user(lab).post(f"/orders/lab/{order.pk}/collect/")
    as_user(lab).post(
        f"/orders/lab/{order.pk}/result/", {"value": "Positive", "flag": "abnormal", "comment": "P. falciparum"}
    )
    page = d.get(f"/clinical/consult/{with_doctor.pk}/").content.decode()
    assert "Positive" in page and "Result in" in page


def test_corrected_result_keeps_the_old_one(with_doctor, doctor, lab, malaria, as_user):
    visit = visit_of(with_doctor)
    order = Order.objects.create(visit=visit, test=malaria)
    c = as_user(lab)
    c.post(f"/orders/lab/{order.pk}/result/", {"value": "Negative", "flag": "normal"})
    c.post(f"/orders/lab/{order.pk}/correct/", {"value": "Positive", "flag": "abnormal", "comment": "Slide re-read"})
    assert Result.objects.filter(order=order).count() == 2
    assert order.current_result.value == "Positive"
    assert [r.value for r in order.current_result.earlier] == ["Negative"]
    page = as_user(doctor).get(f"/clinical/consult/{with_doctor.pk}/").content.decode()
    assert "Positive" in page and "Earlier result (corrected)" in page


def test_correction_needs_a_reason_and_results_are_immutable(with_doctor, lab, malaria, as_user):
    order = Order.objects.create(visit=visit_of(with_doctor), test=malaria)
    c = as_user(lab)
    c.post(f"/orders/lab/{order.pk}/result/", {"value": "Negative", "flag": "normal"})
    c.post(f"/orders/lab/{order.pk}/correct/", {"value": "Positive", "flag": "abnormal", "comment": ""})
    assert order.results.count() == 1
    r = order.results.get()
    r.value = "edited"
    with pytest.raises(PermissionError):
        r.save()
    with pytest.raises(PermissionError):
        r.delete()


def test_cannot_result_a_cancelled_or_resulted_order_twice(with_doctor, lab, malaria, as_user):
    order = Order.objects.create(visit=visit_of(with_doctor), test=malaria)
    c = as_user(lab)
    c.post(f"/orders/lab/{order.pk}/result/", {"value": "Negative", "flag": "normal"})
    c.post(f"/orders/lab/{order.pk}/result/", {"value": "Positive", "flag": "abnormal"})
    assert order.results.count() == 1


# ------------------------------------------------------------------------------------------------ prescriptions
RX = {"dose": "1 g", "units_per_dose": "2", "route": "", "frequency": "tds", "days": "3"}


def test_prescription_quantity_is_calculated(with_doctor, doctor, paracetamol, as_user):
    visit = visit_of(with_doctor)
    as_user(doctor).post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": paracetamol.pk})
    assert Prescription.objects.get(visit=visit).quantity == Decimal("18")


def test_allergy_match_cannot_be_saved_without_acknowledgement(with_doctor, doctor, amoxicillin, patient, as_user):
    patient.allergies = "Penicillin"
    patient.save()
    visit = visit_of(with_doctor)
    c = as_user(doctor)
    r = c.post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": amoxicillin.pk})
    assert r.status_code == 200 and b"Allergy warning" in r.content
    assert not Prescription.objects.filter(visit=visit).exists()
    c.post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": amoxicillin.pk, "override_allergy": "on"})  # no reason
    assert not Prescription.objects.filter(visit=visit).exists()
    c.post(
        f"/rx/visits/{visit.pk}/add/",
        {
            **RX,
            "drug": amoxicillin.pk,
            "override_allergy": "on",
            "override_reason": "Life-threatening infection, no alternative",
        },
    )
    rx = Prescription.objects.get(visit=visit)
    assert (
        rx.allergy_warning and rx.allergy_acknowledged_by == doctor and "no alternative" in rx.allergy_override_reason
    )


def test_no_warning_for_unrelated_drug(with_doctor, doctor, paracetamol, patient, as_user):
    patient.allergies = "Penicillin"
    patient.save()
    visit = visit_of(with_doctor)
    as_user(doctor).post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": paracetamol.pk})
    assert not Prescription.objects.get(visit=visit).allergy_warning


def test_same_drug_cannot_be_prescribed_twice(with_doctor, doctor, paracetamol, as_user):
    visit = visit_of(with_doctor)
    c = as_user(doctor)
    c.post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": paracetamol.pk})
    c.post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": paracetamol.pk})
    assert Prescription.objects.filter(visit=visit).count() == 1


def test_closed_visit_accepts_nothing_more(with_doctor, doctor, icd, paracetamol, as_user):
    visit = visit_of(with_doctor)
    VisitDiagnosis.objects.create(visit=visit, icd10=icd, added_by=doctor)
    c = as_user(doctor)
    c.post(f"/clinical/consult/{with_doctor.pk}/", {"outcome": "home", "action": "finish"})
    visit.refresh_from_db()
    assert visit.status == "closed"
    c.post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": paracetamol.pk})
    assert not Prescription.objects.filter(visit=visit).exists()


# ------------------------------------------------------------------------------------------------ beds
def admit(doctor_client, visit, bed, reason="Severe malaria"):
    return doctor_client.post(f"/wards/admit/visit/{visit.pk}/", {"bed": bed.pk, "reason": reason})


def test_two_admissions_cannot_hold_the_same_bed(beds, patient, db):
    from apps.patients.models import Patient

    other = Patient.objects.create(
        first_name="B", last_name="T", date_of_birth="2000-01-01", sex="M", phone="0711000009"
    )
    v1 = Visit.objects.create(patient=patient)
    v2 = Visit.objects.create(patient=other)
    Admission.objects.create(patient=patient, visit=v1, bed=beds[0], reason="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Admission.objects.create(patient=other, visit=v2, bed=beds[0], reason="y")


def test_a_patient_cannot_be_admitted_twice(beds, patient, db):
    Admission.objects.create(patient=patient, visit=Visit.objects.create(patient=patient), bed=beds[0], reason="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Admission.objects.create(patient=patient, visit=Visit.objects.create(patient=patient), bed=beds[1], reason="y")


def test_full_inpatient_case_admit_transfer_discharge(with_doctor, doctor, nurse, icd, beds, as_user):
    visit = visit_of(with_doctor)
    VisitDiagnosis.objects.create(visit=visit, icd10=icd, added_by=doctor)
    d, n = as_user(doctor), as_user(nurse)
    assert admit(d, visit, beds[0]).status_code == 302
    adm = Admission.objects.get(visit=visit)
    beds[0].refresh_from_db()
    assert adm.number.startswith("ADM-") and beds[0].status == "occupied"
    with_doctor.refresh_from_db()
    assert with_doctor.stage == "admitted"
    assert d.get(f"/wards/admissions/{adm.pk}/").status_code == 200

    # nurse records ward observations and a progress note; doctor cannot transfer
    n.post(
        f"/clinical/visits/{visit.pk}/vitals/add/", {"temperature": "37.2", "pulse": "80", "blood_pressure": "120/80"}
    )
    n.post(f"/clinical/visits/{visit.pk}/notes/add/", {"text": "Comfortable, taking fluids."})
    assert visit.vitals.filter(context="ward").count() == 1
    assert d.post(f"/wards/visits/{visit.pk}/transfer/", {"bed": beds[1].pk}).status_code == 403

    n.post(f"/wards/visits/{visit.pk}/transfer/", {"bed": beds[1].pk})
    adm.refresh_from_db()
    for b in beds:
        b.refresh_from_db()
    assert adm.bed == beds[1] and (beds[0].status, beds[1].status) == ("cleaning", "occupied")
    assert [str(m) for m in BedMove.objects.filter(admission=adm)] == [
        "admitted to Test ward bed T1",
        "Test ward bed T1 to Test ward bed T2",
    ]
    # the cleaning bed is not offered until a nurse releases it
    assert beds[0].pk not in [b.pk for b in __import__("apps.wards.forms", fromlist=["x"]).free_beds()]
    n.post(f"/wards/beds/{beds[0].pk}/ready/")
    beds[0].refresh_from_db()
    assert beds[0].status == "free"

    r = d.post(
        f"/wards/visits/{visit.pk}/discharge/",
        {"condition_at_discharge": "improved", "summary": "Treated with artesunate.", "follow_up": "Review in 1 week"},
    )
    assert r.status_code == 302
    adm.refresh_from_db()
    beds[1].refresh_from_db()
    with_doctor.refresh_from_db()
    assert adm.status == "discharged" and beds[1].status == "cleaning" and with_doctor.stage == "done"
    assert d.get(f"/wards/admissions/{adm.pk}/summary/").status_code == 200
    assert AuditLog.objects.filter(action="print", object_id=str(visit.pk)).exists()


def test_discharge_needs_a_diagnosis(with_doctor, doctor, beds, as_user):
    visit = visit_of(with_doctor)
    admit(as_user(doctor), visit, beds[0])
    r = as_user(doctor).post(
        f"/wards/visits/{visit.pk}/discharge/", {"condition_at_discharge": "improved", "summary": "x", "follow_up": "y"}
    )
    assert r.status_code == 302 and Admission.objects.get(visit=visit).status == "admitted"


def test_direct_admission_from_the_patient_page(doctor, patient, beds, as_user):
    c = as_user(doctor)
    assert c.get(f"/wards/admit/patient/{patient.pk}/").status_code == 200
    c.post(f"/wards/admit/patient/{patient.pk}/", {"bed": beds[2].pk, "reason": "Elective admission"})
    adm = Admission.objects.get(patient=patient)
    assert adm.visit.kind == "inpatient" and adm.bed == beds[2]


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_admissions_to_one_bed_only_one_wins():
    from apps.core.models import User
    from apps.patients.models import Patient
    from apps.wards.models import Ward

    doc = User.objects.create_user("simdoc", password="pw-1234567", role="doctor")
    bed = Bed.objects.create(ward=Ward.objects.create(name="Race ward"), number="R1")
    visits = []
    for i in range(2):
        p = Patient.objects.create(
            first_name=f"R{i}", last_name="Race", date_of_birth="2000-01-01", sex="F", phone=f"07120000{i:02d}"
        )
        visits.append(Visit.objects.create(patient=p))
    barrier, results = threading.Barrier(2), []

    def attempt(visit):
        try:
            client = Client()
            client.force_login(doc)
            barrier.wait()
            client.post(f"/wards/admit/visit/{visit.pk}/", {"bed": bed.pk, "reason": "Race"})
            results.append("done")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(v,)) for v in visits]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert Admission.objects.filter(bed=bed, status="admitted").count() == 1


# ------------------------------------------------------------------------------------------------ audit and access
def test_opening_clinical_records_writes_view_entries(with_doctor, doctor, nurse, beds, icd, patient, as_user):
    visit = visit_of(with_doctor)
    as_user(doctor).get(f"/clinical/consult/{with_doctor.pk}/")
    as_user(doctor).get(f"/clinical/patients/{patient.pk}/history/")
    as_user(doctor).get(f"/clinical/visits/{visit.pk}/")
    as_user(nurse).get(f"/clinical/consult/{with_doctor.pk}/")
    views = AuditLog.objects.filter(action="view")
    assert views.filter(object_id=str(visit.pk), user=doctor).count() >= 2
    assert views.filter(object_id=str(patient.pk), user=doctor).exists()
    assert views.filter(object_id=str(visit.pk), user=nurse).exists()
    VisitDiagnosis.objects.create(visit=visit, icd10=icd, added_by=doctor)
    admit(as_user(doctor), visit, beds[0])
    adm = Admission.objects.get(visit=visit)
    before = views.count()
    as_user(doctor).get(f"/wards/admissions/{adm.pk}/")
    assert views.count() == before + 1


def test_break_glass_flow(patient, with_doctor, as_user, reception):
    c = as_user(reception)
    url = f"/clinical/patients/{patient.pk}/history/"
    assert c.get(url).status_code == 403
    assert c.post(f"/clinical/patients/{patient.pk}/break-glass/", {"reason": "short"}).status_code == 403
    r = c.post(f"/clinical/patients/{patient.pk}/break-glass/", {"reason": "Patient unconscious, need allergy history"})
    assert r.status_code == 302
    entry = AuditLog.objects.get(action="break_glass")
    assert entry.needs_review and entry.user == reception and "unconscious" in entry.reason
    assert c.get(url).status_code == 200
    s = c.session
    s["break_glass"] = {str(patient.pk): 0}  # grant long expired
    s.save()
    assert c.get(url).status_code == 403


def test_history_shows_earlier_visits_and_results_in_two_clicks(with_doctor, doctor, lab, malaria, patient, as_user):
    visit = visit_of(with_doctor)
    o = Order.objects.create(visit=visit, test=malaria)
    as_user(lab).post(f"/orders/lab/{o.pk}/result/", {"value": "Positive", "flag": "abnormal"})
    d = as_user(doctor)
    detail = d.get(f"/patients/{patient.pk}/")  # click 1: patient page has the link
    assert f"/clinical/patients/{patient.pk}/history/" in detail.content.decode()
    hist = d.get(f"/clinical/patients/{patient.pk}/history/")  # click 2 shows the result
    assert b"Positive" in hist.content and b"Malaria test" in hist.content


# ------------------------------------------------------------------------------------------------ the two exit-gate cases
def test_full_outpatient_case_registration_to_completed(
    clinic, reception, nurse, doctor, lab, malaria, paracetamol, icd, as_user
):
    from apps.patients.models import Patient

    r = as_user(reception).post(
        "/patients/new/",
        {"first_name": "Amina", "last_name": "Hassan", "date_of_birth": "1974-08-21", "sex": "F", "id_type": "none",
         "phone": "0722 555 111", "payer_type": "cash", "add_to_queue": "1", "clinic": clinic.pk},
    )  # fmt: skip
    assert r.status_code == 302
    entry = Patient.objects.get(first_name="Amina").queue_entries.get()
    as_user(nurse).post(f"/clinical/triage/{entry.pk}/", TRIAGE)
    as_user(doctor).post(f"/clinical/call-in/{entry.pk}/")
    visit = visit_of(entry)
    d = as_user(doctor)
    d.post(
        f"/clinical/consult/{entry.pk}/",
        {"history": "Chills", "examination": "Pyrexial", "plan": "Treat malaria", "action": "save"},
    )
    d.post(f"/orders/visits/{visit.pk}/add/", {"test": malaria.pk})
    d.post(f"/clinical/visits/{visit.pk}/send-to-lab/")
    order = Order.objects.get(visit=visit)
    as_user(lab).post(f"/orders/lab/{order.pk}/result/", {"value": "Positive", "flag": "abnormal"})
    entry.refresh_from_db()
    assert entry.stage == "with_doctor"
    d.post(f"/clinical/visits/{visit.pk}/diagnoses/add/", {"icd10": icd.pk})
    d.post(f"/rx/visits/{visit.pk}/add/", {**RX, "drug": paracetamol.pk})
    d.post(f"/clinical/consult/{entry.pk}/", {"outcome": "home", "review_in_days": "3", "action": "finish"})
    entry.refresh_from_db()
    visit.refresh_from_db()
    assert entry.stage == "done" and visit.status == "closed" and visit.review_in_days == 3
    assert Prescription.objects.filter(visit=visit, status="pending").count() == 1


# ------------------------------------------------------------------------------------------------ pages render
def test_every_clinical_page_renders(
    with_doctor, doctor, nurse, lab, admin_user, malaria, paracetamol, icd, beds, as_user
):
    visit = visit_of(with_doctor)
    Order.objects.create(visit=visit, test=malaria)
    VisitDiagnosis.objects.create(visit=visit, icd10=icd, added_by=doctor)
    Prescription.objects.create(
        visit=visit, drug=paracetamol, dose="1 g", units_per_dose=2, days=3, prescribed_by=doctor
    )
    d = as_user(doctor)
    assert d.get(f"/clinical/visits/{visit.pk}/icd10/?q=malar").status_code == 200
    assert b"B50.9" in d.get(f"/clinical/visits/{visit.pk}/icd10/?q=malar").content
    for u in (
        f"/clinical/consult/{with_doctor.pk}/",
        "/orders/lab/",
        "/wards/",
        "/rx/",
        f"/wards/admit/visit/{visit.pk}/",
    ):
        assert d.get(u).status_code == 200, u
    assert as_user(lab).get("/orders/lab/").status_code == 200
    admit(d, visit, beds[0])
    adm = Admission.objects.get(visit=visit)
    for c in (d, as_user(nurse), as_user(admin_user)):
        assert c.get(f"/wards/admissions/{adm.pk}/").status_code == 200
    assert as_user(nurse).get("/wards/").status_code == 200


def test_choosing_admit_goes_straight_to_the_ward_screen_even_without_a_diagnosis(with_doctor, doctor, beds, as_user):
    c = as_user(doctor)
    r = c.post(f"/clinical/consult/{with_doctor.pk}/", {"outcome": "admit", "action": "finish"})
    visit = visit_of(with_doctor)
    assert r.status_code == 302 and r.url == f"/wards/admit/visit/{visit.pk}/"
    page = c.get(r.url)
    assert page.status_code == 200 and b"Test ward bed T1" in page.content
