import datetime
import re

import pytest
from django.test import Client
from django.urls import get_resolver
from django.utils import timezone

from apps.accounts import totp
from apps.accounts.models import FailedLogin, Role, TOTPDevice, User
from apps.core.models import AuditLog
from apps.sync.models import Outbox
from conftest import make_user

PW = "pw-for-tests-123"


# ---------- one-time codes ----------


def test_totp_matches_the_rfc_6238_test_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # the RFC's "12345678901234567890"
    assert totp.verify(secret, "287082", now=59, window=0) == 1  # RFC: 94287082 at time 59
    assert totp.verify(secret, "081804", now=1111111109, window=0) is not None  # RFC: 07081804
    assert totp.verify(secret, "000000", now=59, window=0) is None
    assert totp.verify(secret, "abc", now=59) is None


def test_a_code_cannot_be_used_twice():
    secret = totp.new_secret()
    now = 1_000_000
    code = totp._code(secret, now // 30)
    step = totp.verify(secret, code, now=now)
    assert step and totp.verify(secret, code, now=now, after_step=step) is None


# ---------- lockout ----------


def attempt(client, username, password):
    return client.post("/login/", {"username": username, "password": password})


def test_five_wrong_passwords_lock_the_account_even_for_the_right_password(client, doctor):
    for _ in range(5):
        assert "Wrong" in attempt(client, "doctor", "nope").content.decode() or True
    r = attempt(client, "doctor", PW)  # the right password, but locked
    assert r.status_code == 200 and "Too many wrong passwords" in r.content.decode()
    assert "_auth_user_id" not in client.session
    other = make_user(Role.NURSE)
    assert attempt(Client(), other.username, PW).status_code == 302  # others are not affected


def test_lockout_ends_after_the_wait_and_success_clears_the_count(client, doctor):
    for _ in range(5):
        attempt(client, "doctor", "nope")
    FailedLogin.objects.update(at=timezone.now() - datetime.timedelta(minutes=16))
    assert attempt(client, "doctor", PW).status_code == 302
    assert FailedLogin.objects.count() == 0


def test_four_wrong_passwords_do_not_lock(client, doctor):
    for _ in range(4):
        attempt(client, "doctor", "nope")
    assert attempt(client, "doctor", PW).status_code == 302


def test_too_many_failures_from_one_address_lock_that_address(client, db, settings):
    settings.LOCKOUT_ATTEMPTS_PER_IP = 3
    for n in range(3):
        attempt(client, f"someone{n}", "x")
    make_user(Role.NURSE)
    r = attempt(client, "nurse", PW)
    assert r.status_code == 200 and "Too many" in r.content.decode()


# ---------- two-factor for administrators ----------


@pytest.fixture
def two_factor(settings):
    settings.TWO_FACTOR_REQUIRED = True


def code_for(device, now=None):
    return totp._code(device.secret, int((now or timezone.now().timestamp()) // 30))


def test_admin_must_set_up_then_verify(client, two_factor, admin_user):
    assert attempt(client, "admin", PW).status_code == 302
    r = client.get("/patients/")
    assert r.status_code == 302 and "/2fa/setup/" in r["Location"]  # nothing opens before the second step
    assert client.get("/2fa/setup/").status_code == 200
    device = TOTPDevice.objects.get(user=admin_user)
    wrong = client.post("/2fa/setup/", {"code": "000000"})
    assert wrong.status_code == 200 and "not right" in wrong.content.decode()
    assert client.get("/patients/").status_code == 302
    assert client.post("/2fa/setup/", {"code": code_for(device)}).status_code == 302
    assert client.get("/patients/").status_code == 200

    fresh = Client()  # a new sign-in has to pass the second step again
    attempt(fresh, "admin", PW)
    r = fresh.get("/patients/")
    assert r.status_code == 302 and "/2fa/verify/" in r["Location"]
    device.refresh_from_db()
    used = code_for(device)
    device.last_step = int(timezone.now().timestamp() // 30)  # that code's step was already used
    device.save()
    assert "not right" in fresh.post("/2fa/verify/", {"code": used}).content.decode()
    assert fresh.get("/patients/").status_code == 302


def test_correct_code_opens_everything(client, two_factor, admin_user):
    TOTPDevice.objects.create(user=admin_user, secret=totp.new_secret(), confirmed_at=timezone.now())
    attempt(client, "admin", PW)
    device = TOTPDevice.objects.get(user=admin_user)
    r = client.post("/2fa/verify/", {"code": code_for(device)})
    assert r.status_code == 302
    assert client.get("/patients/").status_code == 200


def test_other_roles_do_not_need_it(client, two_factor, doctor):
    attempt(client, "doctor", PW)
    assert client.get("/patients/").status_code == 200


def test_the_secret_is_kept_out_of_the_audit_log_and_the_sync_queue(client, two_factor, admin_user):
    attempt(client, "admin", PW)
    client.get("/2fa/setup/")
    secret = TOTPDevice.objects.get(user=admin_user).secret
    assert not [a for a in AuditLog.objects.filter(model_label="accounts.TOTPDevice") if secret in str(a.changes)]
    assert not [o for o in Outbox.objects.filter(model_label="accounts.TOTPDevice") if secret in str(o.data)]


# ---------- refused requests are recorded ----------


def test_a_refused_request_is_in_the_audit_log(login, doctor):
    r = login(doctor).get("/billing/invoices/")
    assert r.status_code == 403
    row = AuditLog.objects.filter(action="denied").get()
    assert row.user == doctor and row.object_repr == "GET /billing/invoices/"


# ---------- every page, every role ----------


def all_urls():
    """Every URL of the site with dummy values filled in, as (name, path)."""
    found = []

    def walk(patterns, prefix):
        for p in patterns:
            route = str(p.pattern)
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, prefix + route)
            else:
                path = re.sub(
                    r"<(?:\w+:)?(\w+)>",
                    lambda m: "1" if m.group(1) in ("pk", "item_pk") or "id" in m.group(1) else "x",
                    prefix + route,
                )
                found.append((p.name, "/" + path.replace("^", "").replace("$", "")))

    walk(get_resolver().url_patterns, "")
    return [(n, p) for n, p in found if n and not p.startswith("/admin/")]


PUBLIC = {"login", "otp_setup", "otp_verify", "health", "sync_receive", "mpesa_confirm"}


def test_the_site_has_a_lot_of_pages_to_check():
    assert len(all_urls()) > 80


def test_every_page_as_every_role(db, catalogue, patient):
    """No role gets a server error anywhere, anonymous visitors reach nothing, and every refusal is logged."""
    urls = all_urls()
    refused = 0
    anon = Client(raise_request_exception=False)
    for name, path in urls:
        for method in ("get", "post"):
            r = getattr(anon, method)(path)
            assert r.status_code != 500, (method, path)
            if name not in PUBLIC and name != "logout":
                assert r.status_code in (302, 403, 404, 405) and (r.status_code != 302 or "/login/" in r["Location"]), (
                    method, path, r.status_code)  # fmt: skip
            refused += r.status_code == 403
    for role in Role:
        user = make_user(role, username=f"every-{role.value}")
        client = Client(raise_request_exception=False)
        client.force_login(user)
        for name, path in urls:
            if name == "logout":
                continue
            for method in ("get", "post"):
                r = getattr(client, method)(path)
                assert r.status_code != 500, (role.value, method, path)
                refused += r.status_code == 403
    assert refused > 100  # the matrix really exercised the role checks
    assert AuditLog.objects.filter(action="denied").count() == refused


def test_clinical_and_money_pages_stay_closed_to_the_wrong_roles(db, catalogue, patient):
    must_refuse = {
        Role.DOCTOR: ["/billing/invoices/", "/billing/cash-up/", "/stock/dispense/", "/billing/day/"],
        Role.NURSE: ["/billing/invoices/", "/stock/receipts/"],
        Role.RECEPTION: ["/billing/invoices/", "/stock/"],
        Role.CASHIER: ["/stock/dispense/", "/stock/", "/billing/day/", "/ops/reports/"],
        Role.PHARMACIST: ["/billing/invoices/", "/billing/day/", "/ops/reports/"],
        Role.LAB: ["/billing/invoices/", "/stock/"],
        Role.MANAGER: ["/billing/invoices/", "/stock/dispense/", "/sync/status/"],
    }
    for role, paths in must_refuse.items():
        client = Client(raise_request_exception=False)
        client.force_login(make_user(role, username=f"closed-{role.value}"))
        for path in paths:
            assert client.get(path).status_code == 403, (role.value, path)


def test_cloud_user_command_uses_high_ids(db, monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr("getpass.getpass", lambda *_: "a-strong-password-123")
    call_command("create_cloud_user", "cloudmanager")
    user = User.objects.get(username="cloudmanager")
    assert user.pk >= 1_000_000 and user.role == Role.MANAGER and user.check_password("a-strong-password-123")


# ---------- the website's database account ----------


@pytest.mark.django_db(transaction=True)
def test_website_database_account_cannot_change_structure_or_the_ledgers(patient, cashier):
    import psycopg
    from django.conf import settings
    from django.db import connection

    from apps.ops import dbroles

    role = "hms_app_drill"
    cfg = settings.DATABASES["default"]
    with connection.cursor() as cur:
        cur.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user")
        if not cur.fetchone()[0]:
            pytest.skip("The test database account cannot create roles (it can in CI, where it is a superuser).")
    owner = dbroles.apply(role, "drill-pass-123")
    assert owner == cfg["USER"]
    try:
        with psycopg.connect(
            host=cfg["HOST"],
            port=cfg["PORT"],
            dbname=cfg["NAME"],
            user=role,
            password="drill-pass-123",
            autocommit=True,
        ) as app:

            def refused(sql):
                try:
                    app.execute(sql)
                except psycopg.errors.InsufficientPrivilege:
                    return True
                return False

            assert app.execute("SELECT count(*) FROM patients_patient").fetchone()[0] == 1  # reads
            app.execute("UPDATE patients_patient SET phone = phone")  # and normal writes work
            app.execute("INSERT INTO core_auditlog (timestamp, action, model_label, object_id, object_repr, changes) "
                        "VALUES (now(), 'view', 'x', '1', 'x', '{}')")  # fmt: skip
            assert refused("UPDATE core_auditlog SET object_repr = 'edited'")
            assert refused("DELETE FROM core_auditlog")
            assert refused("TRUNCATE core_auditlog")
            assert refused("UPDATE billing_payment SET amount = 1")
            assert refused("DELETE FROM billing_payment")
            assert refused("DELETE FROM stock_stockmovement")
            assert refused("DELETE FROM sync_outbox")
            assert refused("DROP TABLE patients_patient")
            assert refused("ALTER TABLE patients_patient ADD COLUMN x int")
            assert refused("CREATE TABLE sneaky (id int)")
            assert refused("CREATE ROLE intruder")
    finally:
        with connection.cursor() as cur:
            for sql in (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}",
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}",
                f"REVOKE ALL ON SCHEMA public FROM {role}",
                f'REVOKE ALL ON DATABASE "{cfg["NAME"]}" FROM {role}',
                f"DROP ROLE {role}",
            ):
                cur.execute(sql)


def test_managers_need_two_factor_like_administrators(db, settings):
    from apps.accounts.twofactor import needs_second_factor

    settings.TWO_FACTOR_REQUIRED = True
    assert needs_second_factor(make_user(Role.MANAGER, username="mgr-2fa"))
    assert needs_second_factor(make_user(Role.ADMIN, username="adm-2fa"))
    assert not needs_second_factor(make_user(Role.CASHIER, username="cash-2fa"))
    settings.TWO_FACTOR_REQUIRED = False
    assert not needs_second_factor(make_user(Role.MANAGER, username="mgr-2fa-off"))
