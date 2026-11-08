import datetime

import pytest

from apps.core.models import AuditLog
from apps.patients.duplicates import find_duplicates
from apps.patients.models import Patient, normalize_phone

pytestmark = pytest.mark.django_db

FORM = {
    "first_name": "Brian",
    "last_name": "Otieno",
    "date_of_birth": "2019-05-02",
    "sex": "M",
    "id_type": "none",
    "id_number": "",
    "phone": "0722 111 222",
    "payer_type": "cash",
}


def test_hospital_numbers_are_sequential_and_unique(db):
    a = Patient.objects.create(first_name="A", last_name="One", date_of_birth="2000-01-01", sex="F", phone="0711000001")
    b = Patient.objects.create(first_name="B", last_name="Two", date_of_birth="2000-01-01", sex="M", phone="0711000002")
    assert (a.hospital_number, b.hospital_number) == ("HN-000001", "HN-000002")


@pytest.mark.parametrize("raw", ["0712 345 678", "+254712345678", "712345678", "254-712-345678"])
def test_phone_normalisation(raw):
    assert normalize_phone(raw) == "254712345678"


def test_duplicate_by_phone_even_with_different_format(patient):
    found = find_duplicates(first_name="Someone", last_name="Else", phone="+254712345678")
    assert found and "Same phone number" in found[0]["reasons"]


def test_duplicate_by_id(patient):
    found = find_duplicates(first_name="X", last_name="Y", id_type="national_id", id_number="31234821")
    assert "Same ID number" in found[0]["reasons"]


def test_duplicate_by_similar_name_and_dob(patient):
    found = find_duplicates(first_name="Wanjiro", last_name="Kamau", date_of_birth=datetime.date(1992, 3, 14))
    assert found and "Similar name and date of birth" in found[0]["reasons"]


def test_different_person_same_dob_not_flagged(patient):
    assert find_duplicates(first_name="Peter", last_name="Mwangi", date_of_birth=datetime.date(1992, 3, 14)) == []


def test_register_requires_reception_role(login, cashier):
    assert login(cashier).post("/patients/new/", FORM).status_code == 403


def test_register_creates_patient_cover_and_queue_entry(login, reception, clinic):
    r = login(reception).post("/patients/new/", {**FORM, "add_to_queue": "1", "clinic": clinic.pk})
    assert r.status_code == 302
    p = Patient.objects.get(first_name="Brian")
    assert p.covers.get().payer_type == "cash"
    assert p.queue_entries.count() == 1
    assert AuditLog.objects.filter(action="create", object_id=str(p.pk)).exists()


def test_possible_duplicate_blocks_save_until_confirmed(login, reception, patient):
    data = {**FORM, "first_name": "Wanjiru", "last_name": "Kamau", "phone": "0712345678"}
    r = login(reception).post("/patients/new/", data)
    assert r.status_code == 200 and b"possible match" in r.content
    assert Patient.objects.count() == 1
    r = login(reception).post("/patients/new/", {**data, "confirm_new": "1"})
    assert r.status_code == 302 and Patient.objects.count() == 2


def test_same_id_document_is_rejected_outright(login, reception, patient):
    data = {**FORM, "id_type": "national_id", "id_number": "31234821"}
    r = login(reception).post("/patients/new/", data)
    assert r.status_code == 200 and b"already exists" in r.content


def test_sha_requires_member_number(login, reception):
    r = login(reception).post("/patients/new/", {**FORM, "payer_type": "sha"})
    assert b"Member number is required" in r.content


def test_future_dob_rejected(login, reception):
    r = login(reception).post("/patients/new/", {**FORM, "date_of_birth": "2999-01-01"})
    assert b"cannot be in the future" in r.content


def test_masked_id_never_shows_full_number(patient):
    assert patient.masked_id == "•••4821" and "31234821" not in patient.masked_id


def test_search_by_phone_and_name(login, reception, patient):
    c = login(reception)
    assert b"Wanjiru" in c.get("/patients/?q=0712345678").content
    assert b"Wanjiru" in c.get("/patients/?q=kamau").content
    assert b"No patients found" in c.get("/patients/?q=zzz").content
