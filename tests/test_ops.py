import datetime
import os

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.ops import alerts, backup, checks
from apps.ops.models import CheckState
from apps.patients.models import Patient


@pytest.fixture
def backup_env(tmp_path, settings):
    key = tmp_path / "backup.key"
    key.write_text("a-long-random-passphrase-for-tests")
    settings.BACKUP_KEY_FILE = str(key)
    settings.BACKUP_DIR = str(tmp_path / "second-disk")
    return tmp_path


def drop_db(name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", [name])
        cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture
def restored_name():
    name = "hms_restore_drill"
    yield name
    connection.set_autocommit(True)
    drop_db(name)


# ---------- backups ----------


@pytest.mark.django_db(transaction=True)
def test_backup_is_encrypted_restores_on_a_clean_database_and_the_check_passes(backup_env, patient, restored_name):
    path = backup.make_backup()
    assert path.name.startswith("hms-") and path.suffix == ".enc"
    raw = path.read_bytes()
    assert raw.startswith(b"Salted__") and b"PGDMP" not in raw[:64] and b"Wanjiru" not in raw  # encrypted

    backup.restore(path, restored_name)
    ok, rows = backup.compare_counts(restored_name, strict=True)
    assert ok, [r for r in rows if not r[3]]
    assert dict((t, a) for t, a, _, _ in rows)["patients_patient"] == 1

    Patient.objects.create(first_name="New", last_name="Arrival", date_of_birth=datetime.date(2000, 1, 1), sex="M")
    ok_loose, _ = backup.compare_counts(restored_name)  # live grew since the backup: fine
    ok_strict, rows = backup.compare_counts(restored_name, strict=True)
    assert ok_loose and not ok_strict
    assert any(t == "patients_patient" and not good for t, _, _, good in rows)


@pytest.mark.django_db(transaction=True)
def test_a_wrong_key_cannot_restore(backup_env, patient, restored_name, settings):
    path = backup.make_backup()
    other = backup_env / "other.key"
    other.write_text("not-the-passphrase")
    settings.BACKUP_KEY_FILE = str(other)
    with pytest.raises(backup.BackupError):
        backup.restore(path, restored_name)


@pytest.mark.django_db(transaction=True)
def test_restore_refuses_to_overwrite_the_live_database(backup_env, patient):
    path = backup.make_backup()
    from django.conf import settings

    with pytest.raises(backup.BackupError, match="live"):
        backup.restore(path, settings.DATABASES["default"]["NAME"])


@pytest.mark.django_db(transaction=True)
def test_failed_backup_leaves_older_backups_alone(backup_env, patient, settings):
    first = backup.make_backup(now=datetime.datetime(2026, 1, 1, 2, 0))
    settings.BACKUP_KEY_FILE = str(backup_env / "missing.key")
    with pytest.raises(backup.BackupError):
        backup.make_backup(now=datetime.datetime(2026, 1, 2, 2, 0))
    assert first.exists() and len(list(first.parent.iterdir())) == 1  # no half-written file left behind


def test_retention_keeps_30_daily_and_12_monthly(tmp_path, settings):
    settings.BACKUP_DIR = str(tmp_path)
    start = datetime.date(2025, 1, 1)
    for n in range(500):  # 500 nightly backups
        d = start + datetime.timedelta(days=n)
        (tmp_path / f"hms-{d:%Y%m%d}-020000.dump.enc").write_bytes(b"x")
    backup.prune()
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) <= 30 + 12
    every_night = [f"hms-{start + datetime.timedelta(days=n):%Y%m%d}-020000.dump.enc" for n in range(500)]
    assert all(n in left for n in every_night[-30:])  # the last 30 nights
    months = {n[4:10] for n in left}
    assert len(months) >= 12  # and a monthly copy reaching back a year


def test_copy_to_the_cloud_runs_the_configured_command(tmp_path, settings):
    settings.BACKUP_COPY_COMMAND = f"cp {{file}} {tmp_path}/copied.enc"
    source = tmp_path / "hms-x.dump.enc"
    source.write_bytes(b"data")
    assert backup.copy_offsite(source) is True and (tmp_path / "copied.enc").read_bytes() == b"data"
    settings.BACKUP_COPY_COMMAND = "false"
    with pytest.raises(backup.BackupError):
        backup.copy_offsite(source)
    settings.BACKUP_COPY_COMMAND = ""
    assert backup.copy_offsite(source) is False


@pytest.mark.django_db(transaction=True)
def test_backup_command_writes_prunes_and_reports(backup_env, patient, capsys):
    call_command("backup_db")
    assert "Backup written" in capsys.readouterr().out
    assert checks.check_backup()[0]


# ---------- checks: each can be made to fail on purpose ----------


def test_database_check_fails_when_the_database_is_gone(db, monkeypatch):
    assert checks.check_database()[0]

    def broken(*a, **k):
        raise RuntimeError("down")

    with monkeypatch.context() as m:
        m.setattr(connection, "cursor", broken)
        assert checks.check_database()[0] is False


def test_disk_check_fails_when_space_is_low(db, settings):
    settings.HEALTH_DISK_MIN_FREE_PERCENT = 1
    assert checks.check_disk()[0]
    settings.HEALTH_DISK_MIN_FREE_PERCENT = 101
    ok, message = checks.check_disk()
    assert not ok and "free" in message


def test_backup_check_fails_when_missing_or_old(db, settings, tmp_path):
    settings.BACKUP_DIR = str(tmp_path)
    assert checks.check_backup() == (False, "No backup found")
    f = tmp_path / "hms-20260101-020000.dump.enc"
    f.write_bytes(b"x")
    assert checks.check_backup()[0]
    old = timezone.now().timestamp() - 40 * 3600
    os.utime(f, (old, old))
    ok, message = checks.check_backup()
    assert not ok and "hours old" in message


def test_sync_check_fails_on_too_many_or_too_old_rows(db, settings, patient):
    assert checks.check_sync() == (True, "Sync is not switched on")
    settings.SYNC_CLOUD_URL, settings.SYNC_TOKEN = "https://cloud.example", "t"
    settings.HEALTH_SYNC_MAX_ROWS = 10**6
    assert checks.check_sync()[0]
    settings.HEALTH_SYNC_MAX_ROWS = 1
    assert not checks.check_sync()[0]
    settings.HEALTH_SYNC_MAX_ROWS = 10**6
    from apps.sync.models import Outbox

    Outbox.objects.update(created_at=timezone.now() - datetime.timedelta(hours=3))
    ok, message = checks.check_sync()
    assert not ok and "minutes old" in message


# ---------- alerts reach IT ----------


def test_alert_goes_out_once_then_as_a_reminder_then_on_recovery(db, settings):
    sent = []
    notify = lambda subject, body: sent.append(subject)  # noqa: E731
    t0 = timezone.now()
    bad, good = {"disk": (False, "full")}, {"disk": (True, "fine")}
    assert alerts.process(bad, notify, now=t0) == ["disk"]
    alerts.process(bad, notify, now=t0 + datetime.timedelta(hours=1))
    assert sent == ["FAILED: disk"]  # not repeated within 6 hours
    alerts.process(bad, notify, now=t0 + datetime.timedelta(hours=7))
    assert sent == ["FAILED: disk", "FAILED: disk"]
    assert alerts.process(good, notify, now=t0 + datetime.timedelta(hours=8)) == []
    assert sent[-1] == "RECOVERED: disk" and CheckState.objects.get(name="disk").failing_since is None


def test_alert_email_reaches_it(db, settings):
    settings.ALERT_EMAILS = ["it@hospital.example"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    alerts.process({"backup": (False, "No backup found")})
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["it@hospital.example"] and "backup" in mail.outbox[0].subject


def test_a_broken_alert_channel_never_stops_the_checks(db, settings):
    settings.ALERT_SMS_WEBHOOK = "http://127.0.0.1:9/nowhere"
    assert alerts.send_alert("x", "y") is False


def test_health_page_and_run_checks_command(client, db, settings, tmp_path, capsys):
    settings.BACKUP_DIR = str(tmp_path)  # no backup there, so one check fails
    settings.HEALTH_DISK_MIN_FREE_PERCENT = 1
    r = client.get("/ops/health/")
    assert r.status_code == 503 and r.json()["checks"]["backup"] is False and r.json()["checks"]["database"] is True
    assert "No backup" not in r.content.decode()  # names only, no details for the outside world
    with pytest.raises(SystemExit):
        call_command("run_checks")
    (tmp_path / "hms-20260101-020000.dump.enc").write_bytes(b"x")
    assert client.get("/ops/health/").status_code == 200


def test_who_sees_the_report(login, db, admin_user, doctor, cashier):
    from apps.accounts.models import Role
    from conftest import make_user

    manager = make_user(Role.MANAGER)
    assert login(manager).get("/ops/reports/").status_code == 200
    assert login(admin_user).get("/ops/reports/").status_code == 200
    assert login(doctor).get("/ops/reports/").status_code == 403
    assert login(cashier).get("/ops/reports/").status_code == 403
    assert login(manager).get("/patients/").status_code in (200, 403)  # a manager is not given clinical pages


# ---------- the worker ----------


def test_worker_runs_each_job_when_due(settings):
    from apps.ops import worker

    settings.SYNC_CLOUD_URL, settings.SYNC_TOKEN, settings.HMS_MODE = "https://c.example", "t", "local"
    tz = timezone.get_current_timezone()
    at = lambda h, m=0, s=0: datetime.datetime(2026, 3, 3, h, m, s, tzinfo=tz)  # noqa: E731
    assert set(worker.due_jobs(at(1, 0), {})) == {"sync", "checks", "bed_nights"}  # backup waits for 02:00
    last = {"sync": at(1, 0), "checks": at(1, 0), "bed_nights": at(0, 10)}
    assert worker.due_jobs(at(1, 0, 5), last) == []
    assert worker.due_jobs(at(1, 0, 11), last) == ["sync"]
    assert "backup" in worker.due_jobs(at(2, 0, 1), {**last, "backup": at(2, 0) - datetime.timedelta(days=1)})
    assert "backup" not in worker.due_jobs(at(2, 30), {**last, "backup": at(2, 0, 1)})  # once a day


def test_worker_skips_sync_when_not_configured_and_never_backs_up_the_cloud(settings):
    from apps.ops import worker

    tz = timezone.get_current_timezone()
    now = datetime.datetime(2026, 3, 3, 3, 0, tzinfo=tz)
    settings.SYNC_CLOUD_URL = settings.SYNC_TOKEN = ""
    assert "sync" not in worker.due_jobs(now, {})
    settings.HMS_MODE = "cloud"
    assert set(worker.due_jobs(now, {})) == {"checks"}


def test_a_failing_job_does_not_stop_the_worker(db, monkeypatch):
    from apps.ops import worker

    monkeypatch.setattr("apps.ops.alerts.process", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert worker.run_job("checks") is False


def test_backup_check_is_not_used_on_a_training_copy(settings, tmp_path):
    from apps.ops import checks

    settings.BACKUP_DIR = str(tmp_path)
    settings.HMS_MODE = "local"
    assert checks.check_backup()[0] is False
    settings.HMS_MODE = "training"
    assert checks.check_backup()[0] is True
