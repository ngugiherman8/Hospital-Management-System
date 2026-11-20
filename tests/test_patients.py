import datetime

from apps.appointments.models import Appointment
from apps.patients.forms import PatientForm
from apps.patients.models import Patient, normalize_phone

FORM = {
    "first_name": "Grace",
    "last_name": "Achieng",
    "date_of_birth": "1997-08-02",
    "sex": "F",
    "id_type": "national_id",
    "id_number": "99887766",
    "phone": "0722 000 111",
    "payer": "cash",
}


def test_phone_normalisation():
    assert normalize_phone("0712 345 678") == "+254712345678"
    assert normalize_phone("+254 712 345 678") == "+254712345678"
    assert normalize_phone("712345678") == "+254712345678"
    assert normalize_phone("") == ""


def test_hospital_numbers_are_sequential(db):
    a = Patient.objects.create(first_name="A", last_name="One", date_of_birth="2000-01-01", sex="M", phone="0700000001")
    b = Patient.objects.create(first_name="B", last_name="Two", date_of_birth="2000-01-01", sex="F", phone="0700000002")
    assert a.hospital_number == "HN-000001"
    assert b.hospital_number == "HN-000002"


def test_age(patient):
    patient.date_of_birth = datetime.date.today().replace(year=datetime.date.today().year - 30)
    assert patient.age == 30


def test_duplicates_found_by_phone_id_and_name_dob(patient):
    by_phone = Patient.find_possible_duplicates("X", "Y", phone="+254 712 345 678")
    by_id = Patient.find_possible_duplicates("X", "Y", id_number="12344821")
    by_name = Patient.find_possible_duplicates("wanjiru", "KAMAU", date_of_birth=datetime.date(1992, 3, 14))
    other = Patient.find_possible_duplicates(
        "Someone", "Else", date_of_birth=datetime.date(1980, 1, 1), phone="0799999999"
    )
    assert patient in by_phone and patient in by_id and patient in by_name
    assert patient not in other


def test_search_matches_each_word(patient):
    assert patient in Patient.search("wanjiru kamau")
    assert patient in Patient.search("0712345678")
    assert patient in Patient.search(patient.hospital_number)
    assert patient not in Patient.search("wanjiru otieno")


def test_form_rejects_future_birth_date_and_missing_member_number():
    form = PatientForm({**FORM, "date_of_birth": "2999-01-01", "payer": "sha"})
    assert not form.is_valid()
    assert "date_of_birth" in form.errors
    assert "member_number" in form.errors


def test_form_needs_some_phone_number():
    form = PatientForm({**FORM, "phone": ""})
    assert not form.is_valid()
    assert "phone" in form.errors


def test_reception_registers_patient_and_joins_queue(login, reception):
    response = login(reception).post("/patients/new/", {**FORM, "add_to_queue": "on", "clinic": "general"})
    assert response.status_code == 302
    patient = Patient.objects.get(last_name="Achieng")
    assert patient.phone == "+254722000111"
    visit = Appointment.objects.get(patient=patient)
    assert visit.status == "waiting_triage"
    assert visit.queue_label == "A-001"


def test_duplicate_blocks_save_until_confirmed(login, reception, patient):
    data = {**FORM, "first_name": "Wanjiru", "last_name": "Kamau", "date_of_birth": "1992-03-14", "phone": "0712345678"}
    client = login(reception)
    response = client.post("/patients/new/", data)
    assert response.status_code == 200
    assert b"possible match" in response.content
    assert Patient.objects.count() == 1
    response = client.post("/patients/new/", {**data, "confirm_new": "on"})
    assert response.status_code == 302
    assert Patient.objects.count() == 2


def test_nurse_cannot_register_but_can_view(login, nurse, patient):
    client = login(nurse)
    assert client.get("/patients/new/").status_code == 403
    assert client.post("/patients/new/", FORM).status_code == 403
    assert client.get(f"/patients/{patient.pk}/").status_code == 200
    assert client.get("/patients/").status_code == 200


def test_edit_patient(login, reception, patient):
    data = {
        **FORM,
        "first_name": "Wanjiru",
        "last_name": "Kamau",
        "date_of_birth": "1992-03-14",
        "residence": "Roysambu",
    }
    response = login(reception).post(f"/patients/{patient.pk}/edit/", data)
    assert response.status_code == 302
    patient.refresh_from_db()
    assert patient.residence == "Roysambu"
