import datetime

import pytest
from django.utils import timezone

from apps.appointments.models import Clinic
from apps.core.models import User
from apps.patients.models import Patient


@pytest.fixture
def clinic(db):
    return Clinic.objects.create(name="General outpatient")


def make_user(username, role):
    return User.objects.create_user(username, password="pw-1234567", role=role)


@pytest.fixture
def reception(db):
    return make_user("rec", "receptionist")


@pytest.fixture
def doctor(db):
    return make_user("doc", "doctor")


@pytest.fixture
def nurse(db):
    return make_user("nur", "nurse")


@pytest.fixture
def cashier(db):
    return make_user("cash", "cashier")


@pytest.fixture
def login(client):
    def _login(user):
        client.force_login(user)
        return client

    return _login


@pytest.fixture
def patient(db):
    return Patient.objects.create(
        first_name="Wanjiru",
        last_name="Kamau",
        date_of_birth=datetime.date(1992, 3, 14),
        sex="F",
        id_type="national_id",
        id_number="31234821",
        phone="0712 345 678",
    )


@pytest.fixture
def future(db):
    return timezone.now() + datetime.timedelta(hours=20)


# ---------------------------------------------------------------- Phase 2 fixtures
from django.test import Client  # noqa: E402

from apps.appointments.models import QueueEntry  # noqa: E402
from apps.clinical.models import ICD10Code  # noqa: E402
from apps.orders.models import TestCatalogue  # noqa: E402
from apps.pharmacy_rx.models import Drug  # noqa: E402
from apps.wards.models import Bed, Ward  # noqa: E402


@pytest.fixture
def lab(db):
    return make_user("labtech", "lab")


@pytest.fixture
def pharmacist(db):
    return make_user("pharm", "pharmacist")


@pytest.fixture
def admin_user(db):
    return make_user("boss", "admin")


@pytest.fixture
def as_user():
    """as_user(user) -> a fresh signed-in test client."""

    def _as(user):
        c = Client()
        c.force_login(user)
        return c

    return _as


@pytest.fixture
def malaria(db):
    return TestCatalogue.objects.get_or_create(name="Malaria test", defaults={"kind": "lab"})[0]


@pytest.fixture
def fbc(db):
    return TestCatalogue.objects.get_or_create(name="Full blood count", defaults={"kind": "lab"})[0]


@pytest.fixture
def icd(db):
    return ICD10Code.objects.get_or_create(code="B50.9", defaults={"title": "Malaria, falciparum, unspecified"})[0]


@pytest.fixture
def paracetamol(db):
    return Drug.objects.get_or_create(name="Paracetamol", strength="500 mg", form="tablet")[0]


@pytest.fixture
def amoxicillin(db):
    return Drug.objects.get_or_create(
        name="Amoxicillin", strength="500 mg", form="capsule", defaults={"allergy_keys": "penicillin,amoxicillin"}
    )[0]


@pytest.fixture
def beds(db):
    ward = Ward.objects.create(name="Test ward")
    return [Bed.objects.create(ward=ward, number=n) for n in ("T1", "T2", "T3")]


TRIAGE = {
    "complaint": "Fever and headache for 3 days",
    "duration": "3 days",
    "priority": "routine",
    "temperature": "38.6",
    "pulse": "96",
    "resp_rate": "18",
    "blood_pressure": "118 / 76",
    "spo2": "98",
    "weight_kg": "64",
    "height_cm": "165",
    "pain_score": "3",
}


@pytest.fixture
def waiting(patient, clinic):
    """A patient in today's queue, waiting for triage."""
    return QueueEntry.join(patient=patient, clinic=clinic)[0]


@pytest.fixture
def with_doctor(waiting, nurse, doctor, as_user):
    """The same patient, triaged and called in: ready for the consultation."""
    as_user(nurse).post(f"/clinical/triage/{waiting.pk}/", TRIAGE)
    as_user(doctor).post(f"/clinical/call-in/{waiting.pk}/")
    waiting.refresh_from_db()
    return waiting
