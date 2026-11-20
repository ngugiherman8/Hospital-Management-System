import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Role, User
from apps.patients.models import Patient
from apps.pharmacy_rx.models import Drug
from apps.stock.services import on_hand


@pytest.mark.django_db
def test_training_seed_refuses_on_a_real_server(settings):
    settings.HMS_MODE = "local"
    with pytest.raises(CommandError, match="training"):
        call_command("seed_training")
    assert Patient.objects.count() == 0


@pytest.mark.django_db
def test_training_seed_makes_practice_data_and_a_banner(settings, client):
    settings.HMS_MODE = "training"
    call_command("seed_training", "--patients", "5", verbosity=0)
    call_command("seed_training", "--patients", "5", verbosity=0)  # again: no duplicates
    assert Patient.objects.count() == 5 and User.objects.count() == len(Role)
    assert all(on_hand(d) == 200 for d in Drug.objects.all())
    client.login(username="train-reception", password="practice-2026-go")
    page = client.get("/patients/")
    assert page.status_code == 200 and b"TRAINING COPY" in page.content
