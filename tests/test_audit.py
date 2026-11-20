import pytest

from apps.core.audit import log_access, reset_context, set_context
from apps.core.models import AuditLog
from apps.patients.models import Patient


def test_create_is_logged_with_user(patient, reception):
    tokens = set_context(user=reception, ip="10.0.0.5")
    try:
        p = Patient.objects.create(
            first_name="Brian", last_name="Otieno", date_of_birth="2019-05-01", sex="M", phone="0700111222"
        )
    finally:
        reset_context(tokens)
    entry = AuditLog.objects.get(model_label="patients.Patient", object_id=str(p.pk), action="create")
    assert entry.user == reception
    assert entry.ip_address == "10.0.0.5"
    assert entry.changes["first_name"]["new"] == "Brian"


def test_update_records_old_and_new_values(patient):
    patient.residence = "Kasarani"
    patient.save()
    entry = AuditLog.objects.filter(object_id=str(patient.pk), action="update").first()
    assert entry.changes["residence"] == {"old": "", "new": "Kasarani"}
    assert "first_name" not in entry.changes  # only what changed


def test_save_without_changes_is_not_logged(patient):
    before = AuditLog.objects.filter(action="update").count()
    patient.save()
    assert AuditLog.objects.filter(action="update").count() == before


def test_view_is_logged(patient, reception):
    tokens = set_context(user=reception)
    try:
        log_access(patient)
    finally:
        reset_context(tokens)
    assert AuditLog.objects.filter(action="view", object_id=str(patient.pk), user=reception).exists()


def test_log_entries_cannot_be_changed_or_deleted(patient):
    entry = AuditLog.objects.first()
    entry.object_repr = "tampered"
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()


def test_request_user_and_ip_reach_the_log(login, reception, patient):
    login(reception).get(f"/patients/{patient.pk}/", REMOTE_ADDR="192.168.1.20")
    entry = AuditLog.objects.get(action="view", object_id=str(patient.pk))
    assert entry.user == reception
    assert entry.ip_address == "192.168.1.20"


def test_password_changes_are_not_written_to_the_log(reception):
    reception.set_password("another-password-456")
    reception.save()
    for entry in AuditLog.objects.filter(model_label="accounts.User"):
        assert "password" not in entry.changes
