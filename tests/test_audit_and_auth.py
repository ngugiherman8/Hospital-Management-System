import pytest

from apps.core.audit import log_event
from apps.core.models import AuditLog, LoginEvent

pytestmark = pytest.mark.django_db


def test_create_and_update_are_audited(patient, reception):
    assert AuditLog.objects.filter(action="create", object_id=str(patient.pk)).exists()
    patient.phone = "0799 000 111"
    patient.save()
    entry = AuditLog.objects.filter(action="update", object_id=str(patient.pk)).first()
    assert entry.changes["phone"] == ["0712 345 678", "0799 000 111"]


def test_unchanged_save_writes_no_update_entry(patient):
    before = AuditLog.objects.filter(action="update").count()
    patient.save()
    assert AuditLog.objects.filter(action="update").count() == before


def test_audit_log_is_append_only(patient):
    entry = AuditLog.objects.first()
    entry.reason = "tamper"
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()


def test_view_of_record_is_logged_with_user(login, reception, patient):
    login(reception).get(patient.get_absolute_url())
    entry = AuditLog.objects.get(action="view")
    assert entry.user == reception and entry.object_id == str(patient.pk)


def test_login_history(client, reception):
    client.post("/login/", {"username": "rec", "password": "wrong"})
    client.post("/login/", {"username": "rec", "password": "pw-1234567"})
    kinds = list(LoginEvent.objects.order_by("id").values_list("kind", flat=True))
    assert kinds == ["failed", "login"]


def test_break_glass_flagged_for_review(patient):
    log_event("break_glass", patient, reason="Unconscious patient", needs_review=True)
    assert AuditLog.objects.filter(needs_review=True).count() == 1


def test_anonymous_redirected_to_login(client):
    r = client.get("/patients/")
    assert r.status_code == 302 and "/login/" in r.url
