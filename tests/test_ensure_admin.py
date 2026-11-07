import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import User

pytestmark = pytest.mark.django_db


def test_creates_full_admin_and_is_idempotent(monkeypatch, client):
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "A-long-password-1")
    call_command("ensure_admin")
    call_command("ensure_admin")
    u = User.objects.get(username="boss")
    assert u.is_superuser and u.is_staff and u.role == "admin"
    assert User.objects.filter(username="boss").count() == 1
    assert client.login(username="boss", password="A-long-password-1")
    assert client.get("/admin/").status_code == 200


def test_changed_password_is_applied(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "First-password-1")
    call_command("ensure_admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "Second-password-2")
    call_command("ensure_admin")
    assert User.objects.get(username="boss").check_password("Second-password-2")


def test_skips_when_unset_and_rejects_weak(monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    call_command("ensure_admin")
    assert not User.objects.exists()
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    with pytest.raises(CommandError):
        call_command("ensure_admin")
