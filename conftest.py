import datetime

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.patients.models import Patient


def make_user(role, username=None):
    username = username or role
    return User.objects.create_user(username=username, password="pw-for-tests-123", role=role, first_name=role.title())


@pytest.fixture
def reception(db):
    return make_user(Role.RECEPTION)


@pytest.fixture
def nurse(db):
    return make_user(Role.NURSE)


@pytest.fixture
def doctor(db):
    return make_user(Role.DOCTOR)


@pytest.fixture
def lab(db):
    return make_user(Role.LAB)


@pytest.fixture
def pharmacist(db):
    return make_user(Role.PHARMACIST)


@pytest.fixture
def cashier(db):
    return make_user(Role.CASHIER)


@pytest.fixture
def dentist(db):
    return make_user(Role.DENTIST)


@pytest.fixture
def midwife(db):
    return make_user(Role.MIDWIFE)


@pytest.fixture
def radiographer(db):
    return make_user(Role.RADIOGRAPHER)


@pytest.fixture
def radiologist(db):
    return make_user(Role.RADIOLOGIST)


@pytest.fixture
def catalogue(db):
    """Placeholder tests, modalities, dental procedures and contact schedules."""
    from django.core.management import call_command

    call_command("seed_clinical", verbosity=0)


@pytest.fixture
def admin_user(db):
    return make_user(Role.ADMIN)


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
        id_number="12344821",
        phone="0712345678",
        payer="cash",
    )


@pytest.fixture
def tomorrow_10am():
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    return timezone.make_aware(datetime.datetime.combine(tomorrow, datetime.time(10, 0)))
