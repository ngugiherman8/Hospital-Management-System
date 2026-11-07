import pytest
from django.core.management import call_command

from apps.appointments.models import Appointment, QueueEntry
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db


def test_skipped_unless_enabled(monkeypatch):
    monkeypatch.delenv("SEED_SAMPLE_DATA", raising=False)
    call_command("seed_sample_data")
    assert Patient.objects.count() == 0


def test_adds_data_once(monkeypatch, doctor, clinic):
    monkeypatch.setenv("SEED_SAMPLE_DATA", "true")
    call_command("seed_sample_data")
    call_command("seed_sample_data")
    assert Patient.objects.count() == 8
    assert QueueEntry.objects.count() == 6
    assert QueueEntry.objects.filter(stage="waiting_triage").count() == 2
    assert Appointment.objects.count() == 3
