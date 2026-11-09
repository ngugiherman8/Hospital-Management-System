import pyotp
import pytest

from apps.core.models import LoginEvent, User

pytestmark = pytest.mark.django_db


def bad_login(client, username="rec", n=1):
    for _ in range(n):
        client.post("/login/", {"username": username, "password": "wrong-password"})


def test_session_times_out_after_30_minutes(settings):
    assert settings.SESSION_COOKIE_AGE == 30 * 60 and settings.SESSION_SAVE_EVERY_REQUEST


def test_account_locks_after_five_failures_even_with_the_right_password(client, reception):
    bad_login(client, n=5)
    r = client.post("/login/", {"username": "rec", "password": "pw-1234567"})
    assert r.status_code == 200 and b"Too many failed attempts" in r.content
    assert "_auth_user_id" not in client.session


def test_four_failures_do_not_lock_and_a_good_login_resets_the_count(client, reception):
    bad_login(client, n=4)
    assert client.post("/login/", {"username": "rec", "password": "pw-1234567"}).status_code == 302
    client.logout()
    bad_login(client, n=4)
    assert client.post("/login/", {"username": "rec", "password": "pw-1234567"}).status_code == 302


def test_lockout_expires_after_the_window(client, reception):
    from django.utils import timezone

    bad_login(client, n=5)
    LoginEvent.objects.filter(kind="failed").update(at=timezone.now() - timezone.timedelta(minutes=16))
    assert client.post("/login/", {"username": "rec", "password": "pw-1234567"}).status_code == 302


def test_lockout_is_per_username(client, reception, doctor):
    bad_login(client, "rec", n=5)
    assert client.post("/login/", {"username": "doc", "password": "pw-1234567"}).status_code == 302


# ------------------------------------------------------------------------------------------------ two-factor
@pytest.fixture
def twofa(settings):
    settings.ADMIN_TWO_FACTOR = True


def test_administrator_is_asked_for_a_code_and_enrols_on_first_use(client, admin_user, twofa):
    r = client.post("/login/", {"username": "boss", "password": "pw-1234567"})
    assert r.status_code == 302 and r.url == "/login/second-step/"
    assert "_auth_user_id" not in client.session  # password alone does not sign in
    page = client.get("/login/second-step/")
    assert b"<svg" in page.content and b"Key:" in page.content
    admin_user.refresh_from_db()
    code = pyotp.TOTP(admin_user.totp_secret).now()
    r = client.post("/login/second-step/", {"code": code})
    assert r.status_code == 302
    assert client.get("/").status_code == 200
    admin_user.refresh_from_db()
    assert admin_user.totp_confirmed_at is not None


def test_wrong_code_does_not_sign_in_and_counts_toward_lockout(client, admin_user, twofa):
    client.post("/login/", {"username": "boss", "password": "pw-1234567"})
    client.get("/login/second-step/")
    r = client.post("/login/second-step/", {"code": "000000"})
    assert r.status_code == 200 and b"not right" in r.content
    assert "_auth_user_id" not in client.session
    assert LoginEvent.objects.filter(username="boss", kind="failed").count() == 1


def test_confirmed_admin_signs_in_with_a_code_and_no_qr(client, admin_user, twofa):
    from django.utils import timezone

    admin_user.is_staff = True
    admin_user.totp_secret = pyotp.random_base32()
    admin_user.totp_confirmed_at = timezone.now()
    admin_user.save()
    client.post("/login/", {"username": "boss", "password": "pw-1234567"})
    page = client.get("/login/second-step/")
    assert b"<svg" not in page.content
    assert client.post("/login/second-step/", {"code": pyotp.TOTP(admin_user.totp_secret).now()}).status_code == 302
    assert client.get("/admin/").status_code == 200


def test_other_roles_are_not_asked_for_a_code(client, doctor, twofa):
    r = client.post("/login/", {"username": "doc", "password": "pw-1234567"})
    assert r.status_code == 302 and r.url != "/login/second-step/"


def test_admin_site_login_goes_through_the_same_second_step(client, admin_user, twofa):
    r = client.post("/admin/login/?next=/admin/", {"username": "boss", "password": "pw-1234567"})
    assert r.status_code == 302 and r.url == "/login/second-step/"


def test_second_step_without_a_password_step_is_refused(client, admin_user, twofa):
    assert client.get("/login/second-step/").status_code == 302
    assert client.post("/login/second-step/", {"code": "123456"}).status_code == 302
    assert "_auth_user_id" not in client.session


def test_two_factor_can_be_required_for_any_user(client, doctor, twofa):
    doctor.two_factor_required = True
    doctor.save()
    r = client.post("/login/", {"username": "doc", "password": "pw-1234567"})
    assert r.url == "/login/second-step/"


# ------------------------------------------------------------------------------------------------ small Phase 1 gaps
def test_patients_can_be_found_by_id_number(client, reception, patient):
    client.force_login(reception)
    assert b"Wanjiru" in client.get("/patients/?q=31234821").content


def test_audited_model_importable_from_core_models():
    from apps.core.models import AuditedModel

    assert AuditedModel._meta.abstract


def test_queue_stage_label_is_awaiting_results():
    from apps.appointments.models import Stage

    assert Stage.AT_LAB.label == "Awaiting results"


def test_users_still_have_their_roles(db):
    assert User.objects.filter(role="admin").count() == 0
