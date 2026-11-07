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
        first_name="Wanjiru", last_name="Kamau", date_of_birth=datetime.date(1992, 3, 14), sex="F",
        id_type="national_id", id_number="31234821", phone="0712 345 678")


@pytest.fixture
def future(db):
    return timezone.now() + datetime.timedelta(hours=20)
