import pytest
from django.db import IntegrityError, transaction

from apps.appointments.models import Appointment, Status, TransitionBlocked
from apps.clinical.models import Consultation, Diagnosis, NoteVersion, TriageRecord
from apps.core.models import AuditLog
from apps.orders.models import Order, OrderItem, Result, TestCatalogue, record_result
from apps.pharmacy_rx.models import Drug, Prescription, PrescriptionItem
from apps.wards import models as wards
from apps.wards.models import Admission, Bed, Ward


@pytest.fixture
def visit(patient):
    return Appointment.objects.create(patient=patient, kind="walk_in")


@pytest.fixture
def with_doctor(visit, nurse, doctor):
    TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Fever")
    visit.move_to(Status.WAITING_DOCTOR, nurse)
    visit.move_to(Status.WITH_DOCTOR, doctor)
    return visit


@pytest.fixture
def malaria_test(db):
    return TestCatalogue.objects.create(code="MAL", name="Malaria rapid test", price=200)


@pytest.fixture
def hb_test(db):
    return TestCatalogue.objects.create(code="HB", name="Haemoglobin", unit="g/dL", ref_low=12, ref_high=17)


@pytest.fixture
def bed(db):
    ward = Ward.objects.create(name="Male ward")
    return Bed.objects.create(ward=ward, number="1")


@pytest.fixture
def second_bed(bed):
    return Bed.objects.create(ward=bed.ward, number="2")


# ---------- access ----------

PAGES = [
    ("/clinical/triage/{pk}/", {"nurse", "doctor", "reception"}),
    ("/clinical/visit/{pk}/", {"nurse", "doctor"}),
    ("/orders/lab/", {"lab", "doctor", "nurse"}),
    ("/wards/", {"nurse", "doctor", "reception"}),
]


@pytest.mark.parametrize("url,allowed", PAGES)
def test_pages_are_limited_by_role(client, db, visit, url, allowed):
    url = url.format(pk=visit.pk)
    assert client.get(url).status_code == 302  # anonymous users go to sign-in
    from conftest import make_user

    for role in ("reception", "nurse", "doctor", "lab", "pharmacist", "cashier"):
        client.force_login(make_user(role, username=f"u-{role}"))
        expected = 200 if role in allowed else 403
        assert client.get(url).status_code == expected, (url, role)


@pytest.mark.parametrize(
    "url",
    ["/clinical/visit/1/note/", "/clinical/visit/1/diagnosis/", "/clinical/visit/1/outcome/",
     "/orders/visit/1/order/", "/prescriptions/visit/1/prescribe/", "/wards/visit/1/admit/"],
)  # fmt: skip
def test_doctor_only_actions_reject_other_roles(login, nurse, reception, lab, with_doctor, url):
    url = url.replace("/1/", f"/{with_doctor.pk}/")
    for user in (nurse, reception, lab):
        assert login(user).post(url, {}).status_code == 403


# ---------- triage ----------


def test_out_of_range_vitals_are_flagged(visit, nurse):
    record = TriageRecord.objects.create(
        appointment=visit, nurse=nurse, complaint="Fever", temperature=39.2, pulse=72, spo2=88, bp_systolic=120
    )
    assert set(record.flags()) == {"temperature", "spo2"}


def test_child_ranges_differ_from_adult(db, nurse):
    import datetime

    from apps.patients.models import Patient

    child = Patient.objects.create(
        first_name="Baby",
        last_name="Otieno",
        date_of_birth=datetime.date.today().replace(year=datetime.date.today().year - 3),
        sex="M",
    )
    visit = Appointment.objects.create(patient=child, kind="walk_in")
    record = TriageRecord.objects.create(appointment=visit, nurse=nurse, complaint="Cough", pulse=110, resp_rate=26)
    assert record.flags() == []  # fine for a 3 year old, high for an adult


def test_saving_triage_moves_visit_to_doctor(login, nurse, visit):
    response = login(nurse).post(
        f"/clinical/triage/{visit.pk}/",
        {"temperature": "38.5", "pulse": "90", "complaint": "Headache", "priority": "urgent"},
    )
    assert response.status_code == 302
    visit.refresh_from_db()
    assert visit.status == Status.WAITING_DOCTOR
    assert visit.triage.priority == "urgent"


def test_triage_needs_a_complaint(login, nurse, visit):
    login(nurse).post(f"/clinical/triage/{visit.pk}/", {"pulse": "90", "priority": "routine"})
    visit.refresh_from_db()
    assert visit.status == Status.WAITING_TRIAGE
    assert not TriageRecord.objects.filter(appointment=visit).exists()


def test_only_nurses_save_triage(login, doctor, reception, visit):
    for user in (doctor, reception):
        assert (
            login(user).post(f"/clinical/triage/{visit.pk}/", {"complaint": "x", "priority": "routine"}).status_code
            == 403
        )


def test_emergency_goes_to_doctor_with_a_reason_and_leads_the_queue(login, reception, patient, doctor):
    first = Appointment.objects.create(patient=patient, kind="walk_in")
    second = Appointment.objects.create(patient=patient, kind="walk_in")
    client = login(reception)
    assert client.post(f"/appointments/{second.pk}/fast-track/", {"reason": ""}).status_code == 302
    second.refresh_from_db()
    assert second.status == Status.WAITING_TRIAGE  # a reason is required
    client.post(f"/appointments/{second.pk}/fast-track/", {"reason": "Unconscious, seizure"})
    second.refresh_from_db()
    assert second.status == Status.WAITING_DOCTOR and second.is_emergency
    entry = AuditLog.objects.filter(model_label="appointments.Appointment", object_id=str(second.pk)).first()
    assert entry.changes["emergency_reason"]["new"] == "Unconscious, seizure"
    page = client.get("/appointments/").content.decode()
    assert page.index(second.patient.hospital_number) >= 0
    assert page.index("Emergency") < page.index(first.queue_label + "</b>") if first.queue_label in page else True


# ---------- notes and diagnoses ----------


def test_notes_are_versioned_and_never_overwritten(login, doctor, with_doctor):
    client = login(doctor)
    client.post(f"/clinical/visit/{with_doctor.pk}/note/", {"history": "Fever 3 days", "plan": "Test"})
    client.post(f"/clinical/visit/{with_doctor.pk}/note/", {"history": "Fever 3 days, chills", "plan": "Treat"})
    consultation = with_doctor.consultation
    versions = list(consultation.notes.order_by("version"))
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].history == "Fever 3 days"
    assert consultation.current_note.history.endswith("chills")
    assert AuditLog.objects.filter(model_label="clinical.NoteVersion", action="create").count() == 2
    with pytest.raises(PermissionError):
        versions[0].save()
    with pytest.raises(PermissionError):
        versions[0].delete()


def test_empty_note_is_rejected(login, doctor, with_doctor):
    login(doctor).post(f"/clinical/visit/{with_doctor.pk}/note/", {"history": " "})
    assert not NoteVersion.objects.exists()


def test_icd10_search(login, doctor):
    data = login(doctor).get("/clinical/icd10/?q=malaria").json()
    assert any(r["code"] == "B54" for r in data["results"])


def test_finishing_needs_diagnosis_and_outcome(login, doctor, with_doctor):
    client = login(doctor)
    client.post(f"/appointments/{with_doctor.pk}/status/", {"status": "completed"})
    with_doctor.refresh_from_db()
    assert with_doctor.status == Status.WITH_DOCTOR
    client.post(
        f"/clinical/visit/{with_doctor.pk}/diagnosis/",
        {"icd10_code": "B54", "name": "Malaria", "certainty": "confirmed"},
    )
    client.post(f"/appointments/{with_doctor.pk}/status/", {"status": "completed"})
    with_doctor.refresh_from_db()
    assert with_doctor.status == Status.WITH_DOCTOR  # outcome still missing
    client.post(f"/clinical/visit/{with_doctor.pk}/outcome/", {"outcome": "home"})
    client.post(f"/appointments/{with_doctor.pk}/status/", {"status": "completed"})
    with_doctor.refresh_from_db()
    assert with_doctor.status == Status.COMPLETED


def test_refer_needs_a_destination(login, doctor, with_doctor):
    login(doctor).post(f"/clinical/visit/{with_doctor.pk}/outcome/", {"outcome": "refer"})
    assert with_doctor.consultation.outcome == ""


def test_closed_visit_cannot_be_edited(login, doctor, visit):
    assert login(doctor).post(f"/clinical/visit/{visit.pk}/note/", {"history": "x"}).status_code == 403


# ---------- orders and results ----------


def test_visit_cannot_wait_for_results_without_an_order(with_doctor, doctor):
    with pytest.raises(TransitionBlocked):
        with_doctor.move_to(Status.AWAITING_RESULTS, doctor)


def test_lab_flow_returns_visit_to_doctor(login, doctor, lab, with_doctor, malaria_test, hb_test):
    login(doctor).post(f"/orders/visit/{with_doctor.pk}/order/", {"tests": [malaria_test.pk, hb_test.pk]})
    order = Order.objects.get()
    assert order.number.startswith("LAB-") and order.items.count() == 2
    with_doctor.move_to(Status.AWAITING_RESULTS, doctor)
    first, second = order.items.get(test=malaria_test), order.items.get(test=hb_test)
    client = login(lab)
    client.post(f"/orders/lab/item/{first.pk}/result/", {"value": "Negative"})
    with_doctor.refresh_from_db()
    assert with_doctor.status == Status.AWAITING_RESULTS  # one test still open
    client.post(f"/orders/lab/item/{second.pk}/result/", {"value": "9.5"})
    with_doctor.refresh_from_db()
    assert with_doctor.status == Status.WITH_DOCTOR
    assert second.current_result.flag == Result.Flag.LOW  # worked out from the normal range


def test_corrected_result_keeps_the_old_one(lab, with_doctor, hb_test):
    order = Order.objects.create(appointment=with_doctor)
    item = OrderItem.objects.create(order=order, test=hb_test)
    first = record_result(item, "9.0", lab)
    second = record_result(item, "13.0", lab, comment="Typing error")
    item.refresh_from_db()
    assert item.current_result == second and second.replaces == first
    assert Result.objects.filter(item=item).count() == 2
    with pytest.raises(PermissionError):
        first.save()


def test_cancelled_tests_do_not_block_the_visit(login, doctor, with_doctor, malaria_test):
    login(doctor).post(f"/orders/visit/{with_doctor.pk}/order/", {"tests": [malaria_test.pk]})
    with_doctor.move_to(Status.AWAITING_RESULTS, doctor)
    item = OrderItem.objects.get()
    with pytest.raises(TransitionBlocked):
        with_doctor.move_to(Status.WITH_DOCTOR, doctor)
    login(doctor).post(f"/orders/item/{item.pk}/cancel/")
    with_doctor.refresh_from_db()
    with_doctor.move_to(Status.WITH_DOCTOR, doctor)


def test_order_numbers_are_unique_and_sequential(with_doctor, malaria_test):
    one, two = Order.objects.create(appointment=with_doctor), Order.objects.create(appointment=with_doctor)
    assert one.number != two.number and one.number.endswith("000001") and two.number.endswith("000002")


# ---------- prescriptions ----------


def test_allergy_match_needs_acknowledgement(login, doctor, with_doctor, patient):
    patient.allergies = "Penicillin, sulfa drugs"
    patient.save()
    drug = Drug.objects.create(generic_name="Penicillin V", strength="250 mg", form="Tablet")
    consultation = Consultation.objects.create(appointment=with_doctor, doctor=doctor)
    client = login(doctor)
    data = {"drug": drug.pk, "dose": "1 tablet", "frequency": "4 times a day", "days": 5, "quantity": 20}
    client.post(f"/prescriptions/visit/{with_doctor.pk}/prescribe/", data)
    assert not PrescriptionItem.objects.exists()
    client.post(f"/prescriptions/visit/{with_doctor.pk}/prescribe/", {**data, "acknowledge_allergy": "on"})
    item = PrescriptionItem.objects.get()
    assert item.allergy_warning and item.allergy_acknowledged_by == doctor
    assert consultation.prescriptions.get().dispensing_status == "pending"


def test_model_refuses_allergic_drug_without_acknowledgement(with_doctor, doctor, patient):
    from django.core.exceptions import ValidationError

    patient.allergies = "amoxicillin"
    patient.save()
    drug = Drug.objects.create(generic_name="Amoxicillin", strength="500 mg", form="Capsule")
    consultation = Consultation.objects.create(appointment=with_doctor, doctor=doctor)
    prescription = Prescription.objects.create(consultation=consultation, prescribed_by=doctor)
    with pytest.raises(ValidationError):
        PrescriptionItem.objects.create(
            prescription=prescription, drug=drug, dose="1", frequency="tds", days=5, quantity=15
        )


def test_unrelated_drug_saves_without_warning(login, doctor, with_doctor, patient):
    patient.allergies = "Penicillin"
    patient.save()
    drug = Drug.objects.create(generic_name="Paracetamol", strength="500 mg", form="Tablet")
    Consultation.objects.create(appointment=with_doctor, doctor=doctor)
    login(doctor).post(
        f"/prescriptions/visit/{with_doctor.pk}/prescribe/",
        {"drug": drug.pk, "dose": "2 tablets", "frequency": "tds", "days": 3, "quantity": 18},
    )
    assert PrescriptionItem.objects.get().allergy_warning is False


# ---------- wards ----------


def test_admit_occupies_bed_and_discharge_frees_it(with_doctor, doctor, bed):
    admission = wards.admit(with_doctor, bed, "Severe malaria", doctor)
    bed.refresh_from_db()
    with_doctor.refresh_from_db()
    assert bed.status == Bed.Status.OCCUPIED and with_doctor.status == Status.ADMITTED
    assert admission.number.startswith("ADM-")
    with pytest.raises(TransitionBlocked):
        with_doctor.move_to(Status.COMPLETED, doctor)  # not discharged yet
    wards.discharge(admission, "Recovered, review in 1 week.", doctor)
    bed.refresh_from_db()
    with_doctor.refresh_from_db()
    assert bed.status == Bed.Status.FREE and with_doctor.status == Status.COMPLETED
    assert Admission.objects.get().discharge_summary.startswith("Recovered")


def test_taken_bed_cannot_be_used_again(with_doctor, doctor, nurse, bed, patient):
    wards.admit(with_doctor, bed, "Pneumonia", doctor)
    other = Appointment.objects.create(patient=patient, kind="walk_in")
    with pytest.raises(wards.BedUnavailable):
        wards.admit(other, bed, "Another", doctor)


@pytest.mark.django_db(transaction=True)
def test_database_refuses_two_active_admissions_on_one_bed(patient, doctor):
    import datetime

    from apps.patients.models import Patient

    ward = Ward.objects.create(name="W")
    bed = Bed.objects.create(ward=ward, number="1")
    other = Patient.objects.create(first_name="A", last_name="B", date_of_birth=datetime.date(1990, 1, 1), sex="M")
    v1 = Appointment.objects.create(patient=patient, kind="walk_in")
    v2 = Appointment.objects.create(patient=other, kind="walk_in")
    Admission.objects.create(patient=patient, appointment=v1, bed=bed, reason="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Admission.objects.create(patient=other, appointment=v2, bed=bed, reason="y")


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_admissions_to_one_bed_only_one_wins(doctor, nurse):
    import datetime
    import threading

    from django.db import connection

    from apps.patients.models import Patient

    ward = Ward.objects.create(name="W")
    bed = Bed.objects.create(ward=ward, number="1")
    visits = []
    for i in range(2):
        p = Patient.objects.create(
            first_name=f"P{i}", last_name="Test", date_of_birth=datetime.date(1990, 1, 1), sex="F"
        )
        v = Appointment.objects.create(patient=p, kind="walk_in")
        TriageRecord.objects.create(appointment=v, nurse=nurse, complaint="x")
        v.move_to(Status.WAITING_DOCTOR, nurse)
        v.move_to(Status.WITH_DOCTOR, doctor)
        visits.append(v)
    outcomes = []
    barrier = threading.Barrier(2)

    def run(visit):
        try:
            barrier.wait()
            wards.admit(Appointment.objects.get(pk=visit.pk), bed, "Test", doctor)
            outcomes.append("ok")
        except wards.BedUnavailable:
            outcomes.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(v,)) for v in visits]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["ok", "refused"]
    assert Admission.objects.filter(bed=bed, discharged_at__isnull=True).count() == 1


def test_transfer_moves_patient_and_keeps_history(with_doctor, doctor, nurse, bed, second_bed):
    admission = wards.admit(with_doctor, bed, "Observation", doctor)
    wards.transfer(admission, second_bed, nurse)
    bed.refresh_from_db()
    second_bed.refresh_from_db()
    admission.refresh_from_db()
    assert (bed.status, second_bed.status) == (Bed.Status.FREE, Bed.Status.OCCUPIED)
    assert admission.bed == second_bed
    move = admission.moves.get()
    assert (move.from_bed, move.to_bed, move.moved_by) == (bed, second_bed, nurse)


def test_ward_screens_follow_roles(login, nurse, doctor, reception, with_doctor, bed, second_bed):
    admission = wards.admit(with_doctor, bed, "Observation", doctor)
    assert (
        login(reception).post(f"/wards/admission/{admission.pk}/transfer/", {"bed": second_bed.pk}).status_code == 403
    )
    assert login(doctor).post(f"/wards/admission/{admission.pk}/transfer/", {"bed": second_bed.pk}).status_code == 403
    assert login(nurse).post(f"/wards/admission/{admission.pk}/transfer/", {"bed": second_bed.pk}).status_code == 302
    assert login(nurse).get(f"/wards/admission/{admission.pk}/discharge/").status_code == 403


# ---------- patient page and audit ----------


def test_patient_page_shows_timeline_and_audits_views(login, doctor, with_doctor, patient, malaria_test, lab):
    consultation = Consultation.objects.create(appointment=with_doctor, doctor=doctor, outcome="home")
    Diagnosis.objects.create(consultation=consultation, icd10_code="B54", name="Malaria")
    order = Order.objects.create(appointment=with_doctor)
    item = OrderItem.objects.create(order=order, test=malaria_test)
    record_result(item, "Positive", lab)
    AuditLog.objects.filter(action="view").delete()
    page = login(doctor).get(f"/patients/{patient.pk}/").content.decode()
    assert "Malaria" in page and "Positive" in page
    viewed = set(AuditLog.objects.filter(action="view").values_list("model_label", flat=True))
    assert {"clinical.TriageRecord", "clinical.Consultation", "orders.Result"} <= viewed


def test_non_clinical_staff_do_not_see_notes_or_results(
    login, reception, cashier, doctor, with_doctor, patient, malaria_test, lab
):
    consultation = Consultation.objects.create(appointment=with_doctor, doctor=doctor, outcome="home")
    Diagnosis.objects.create(consultation=consultation, icd10_code="B54", name="Malaria")
    NoteVersion.objects.create(consultation=consultation, plan="Secret plan", author=doctor)
    item = OrderItem.objects.create(order=Order.objects.create(appointment=with_doctor), test=malaria_test)
    record_result(item, "Positive", lab)
    for user in (reception, cashier):
        page = login(user).get(f"/patients/{patient.pk}/").content.decode()
        assert "Malaria" not in page and "Positive" not in page and "Secret plan" not in page


def test_full_outpatient_and_inpatient_cases(login, nurse, doctor, lab, patient, malaria_test, bed):
    visit = Appointment.objects.create(patient=patient, kind="walk_in")
    login(nurse).post(
        f"/clinical/triage/{visit.pk}/", {"complaint": "Fever", "temperature": "39", "priority": "urgent"}
    )
    login(doctor).post(f"/appointments/{visit.pk}/status/", {"status": "with_doctor"})
    c = login(doctor)
    c.post(f"/orders/visit/{visit.pk}/order/", {"tests": [malaria_test.pk]})
    c.post(f"/appointments/{visit.pk}/status/", {"status": "awaiting_results"})
    login(lab).post(f"/orders/lab/item/{OrderItem.objects.get().pk}/result/", {"value": "Positive", "flag": "high"})
    c = login(doctor)
    drug = Drug.objects.create(generic_name="Artemether/Lumefantrine", strength="20/120 mg", form="Tablet")
    c.post(
        f"/prescriptions/visit/{visit.pk}/prescribe/",
        {"drug": drug.pk, "dose": "4 tablets", "frequency": "bd", "days": 3, "quantity": 24},
    )
    c.post(
        f"/clinical/visit/{visit.pk}/diagnosis/", {"icd10_code": "B50.9", "name": "Malaria", "certainty": "confirmed"}
    )
    c.post(f"/clinical/visit/{visit.pk}/outcome/", {"outcome": "admit"})
    c.post(f"/wards/visit/{visit.pk}/admit/", {"bed": bed.pk, "reason": "Severe malaria"})
    visit.refresh_from_db()
    assert visit.status == Status.ADMITTED
    admission = Admission.objects.get()
    c.post(f"/wards/admission/{admission.pk}/discharge/", {"summary": "Recovered."})
    visit.refresh_from_db()
    bed.refresh_from_db()
    assert visit.status == Status.COMPLETED and bed.status == Bed.Status.FREE


def test_visit_screen_renders_for_doctor_and_is_read_only_for_nurse(
    login, doctor, nurse, with_doctor, malaria_test, bed
):
    page = login(doctor).get(f"/clinical/visit/{with_doctor.pk}/").content.decode()
    for text in ("Save as new version", "Order tests", "Add to prescription", "Admit to bed", "Save outcome"):
        assert text in page
    nurse_page = login(nurse).get(f"/clinical/visit/{with_doctor.pk}/").content.decode()
    assert "Save as new version" not in nurse_page and "Order tests" not in nurse_page
