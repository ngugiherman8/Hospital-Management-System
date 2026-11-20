import datetime
import html

import pytest
from django.test import Client

from apps.accounts.models import Role
from apps.core.models import AuditLog
from apps.golive import readiness
from apps.golive.downtime import FORMS
from apps.golive.models import GoNoGo, Issue, PilotDay, add_working_days
from conftest import make_user


def as_role(role, name=None):
    from apps.accounts.models import User

    username = name or f"gl-{role.value}"
    user = User.objects.filter(username=username).first() or make_user(role, username=username)
    c = Client(raise_request_exception=False)
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_anyone_can_report_but_only_leads_can_update():
    nurse = as_role(Role.NURSE)
    resp = nurse.post(
        "/golive/issues/new/", {"title": "Cannot print receipt", "severity": "high", "department": "cashier"}
    )
    assert resp.status_code == 302
    issue = Issue.objects.get()
    assert issue.reported_by.username == "gl-nurse"
    assert nurse.get(f"/golive/issues/{issue.pk}/").status_code == 403
    manager = as_role(Role.MANAGER)
    assert (
        manager.post(f"/golive/issues/{issue.pk}/", {"status": "workaround", "workaround": ""}).status_code == 200
    )  # needs words
    assert (
        manager.post(
            f"/golive/issues/{issue.pk}/", {"status": "workaround", "workaround": "Use the paper book"}
        ).status_code
        == 302
    )
    issue.refresh_from_db()
    assert issue.is_unresolved and issue.workaround
    manager.post(f"/golive/issues/{issue.pk}/", {"status": "fixed", "workaround": ""})
    issue.refresh_from_db()
    assert not issue.is_unresolved and issue.resolved_on == datetime.date.today()


@pytest.mark.django_db
def test_fix_dates_follow_severity_and_skip_weekends():
    assert add_working_days(datetime.date(2026, 11, 13), 3) == datetime.date(2026, 11, 18)  # Friday -> Wednesday
    crit = Issue.objects.create(title="Down", severity="critical", reported_on=datetime.date(2026, 11, 2))
    med = Issue.objects.create(title="Slow", severity="medium")
    assert crit.due_on == datetime.date(2026, 11, 2) and med.due_on is None
    assert crit.overdue
    with pytest.raises(PermissionError):
        crit.delete()


@pytest.mark.django_db
def test_go_no_go_needs_a_name_and_no_open_critical_problem():
    admin = as_role(Role.ADMIN)
    base = {"department": "pharmacy", "agreed_on": "2026-11-13", "note": ""}
    assert not GoNoGo.objects.exists()
    admin.post("/golive/go-no-go/", {**base, "item": "forms", "agreed_by": ""})
    assert not GoNoGo.objects.exists()
    admin.post("/golive/go-no-go/", {**base, "item": "forms", "agreed_by": "Hospital manager"})
    assert GoNoGo.objects.get().agreed_by == "Hospital manager"
    Issue.objects.create(title="Wrong patient shown", severity="critical")
    admin.post("/golive/go-no-go/", {**base, "item": "no_critical", "agreed_by": "Project lead"})
    assert GoNoGo.objects.count() == 1  # refused while a critical problem is open
    manager = as_role(Role.MANAGER)
    assert manager.get("/golive/go-no-go/").status_code == 200  # may look
    manager.post("/golive/go-no-go/", {**base, "item": "rollback", "agreed_by": "Me"})
    assert GoNoGo.objects.count() == 1  # may not record
    admin.post("/golive/go-no-go/", {"withdraw": GoNoGo.objects.get().pk})
    assert not GoNoGo.objects.exists()


@pytest.mark.django_db
def test_paper_against_system_shows_differences(db, patient):
    from apps.appointments.models import Appointment

    Appointment.objects.create(patient=patient, visit_date=datetime.date.today(), clinic="general")
    manager = as_role(Role.MANAGER)
    manager.post(
        "/golive/pilot/",
        {"day": str(datetime.date.today()), "department": "reception", "paper_cash": "0", "paper_visits": "2",
         "paper_dispensed": "0", "explanation": ""},
    )  # fmt: skip
    day = PilotDay.objects.get()
    rows = {r["label"]: r for r in day.comparison()}
    assert rows["Visits"]["system"] == 1 and rows["Visits"]["difference"] == -1
    assert day.needs_explaining and not day.matches
    assert "Differs, not explained" in manager.get("/golive/pilot/").content.decode()
    manager.post(
        "/golive/pilot/",
        {"day": str(datetime.date.today()), "department": "reception", "paper_cash": "0", "paper_visits": "2",
         "paper_dispensed": "0", "explanation": "One patient left before registering"},
    )  # fmt: skip
    day.refresh_from_db()
    assert PilotDay.objects.count() == 1 and not day.needs_explaining


@pytest.mark.django_db
def test_every_downtime_form_prints_for_every_role():
    for slug in FORMS:
        for role in Role:
            r = as_role(role).get(f"/golive/forms/{slug}/")
            assert r.status_code == 200 and html.escape(FORMS[slug]["title"]).encode() in r.content
    assert as_role(Role.NURSE).get("/golive/forms/nonsense/").status_code == 404


@pytest.mark.django_db
def test_lead_pages_are_closed_to_clinical_roles():
    for path in [
        "/golive/setup/",
        "/golive/audit-review/",
        "/golive/weekly/",
        "/golive/pilot/",
        "/golive/training/",
        "/golive/go-no-go/",
    ]:
        assert as_role(Role.DOCTOR, "d").get(path).status_code == 403, path
        assert as_role(Role.MANAGER, "m").get(path).status_code == 200, path
        assert as_role(Role.ADMIN, "a").get(path).status_code == 200, path


@pytest.mark.django_db
def test_access_review_finds_odd_hours_refusals_and_heavy_use(patient):
    from django.utils import timezone

    from apps.patients.models import Patient

    nurse = make_user(Role.NURSE, username="nightnurse")
    night = timezone.make_aware(datetime.datetime.combine(datetime.date.today(), datetime.time(2, 30)))
    log = AuditLog.objects.create(
        user=nurse, action="view", model_label="patients.Patient", object_id=str(patient.pk), object_repr="NIGHT-VIEW"
    )
    AuditLog.objects.filter(pk=log.pk).update(timestamp=night)
    for i in range(6):  # six different patients in one day by one person
        p = Patient.objects.create(first_name=f"P{i}", last_name="Heavy", date_of_birth="1990-01-01", sex="F")
        AuditLog.objects.create(
            user=nurse, action="view", model_label="patients.Patient", object_id=str(p.pk), object_repr="x"
        )
    as_role(Role.DOCTOR, "snooper").get("/golive/setup/")  # refused, so logged as denied
    page = as_role(Role.ADMIN, "a").get("/golive/audit-review/?threshold=5").content.decode()
    assert "NIGHT-VIEW" in page
    assert "snooper" in page
    assert "nightnurse" in page and "7 patients" in page


@pytest.mark.django_db
def test_setup_check_reports_what_is_missing_and_passes_when_complete(settings):
    settings.HMS_MODE = "training"
    first = {label: ok for ok, label, _ in readiness.run()}
    assert first["Every role has at least one active account"] is False
    assert first["Wards and beds are entered"] is False
    assert not readiness.ready()
    page = as_role(Role.ADMIN, "a").get("/golive/setup/").content.decode()
    assert "Fix" in page


@pytest.mark.django_db
def test_demo_accounts_are_flagged(settings):
    settings.HMS_MODE = "training"
    make_user(Role.DOCTOR)  # username "doctor", what seed_demo makes
    results = {label: (ok, detail) for ok, label, detail in readiness.run()}
    ok, detail = results["No demo accounts are left"]
    assert not ok and "doctor" in detail


@pytest.mark.django_db
def test_full_setup_passes_the_check(settings, tmp_path):
    from django.core.management import call_command

    settings.HMS_MODE = "training"
    settings.HOSPITAL_NAME = "Test Hospital"
    (tmp_path / "users.csv").write_text("username,role\n" + "\n".join(f"u-{r.value},{r.value}" for r in Role) + "\n")
    (tmp_path / "drugs.csv").write_text("generic_name,strength,form,price\nParacetamol,500mg,Tablet,5\n")
    (tmp_path / "prices.csv").write_text("code,name,category,payer,amount\nREG,Registration,registration,cash,200\n")
    (tmp_path / "wards.csv").write_text("ward,kind,beds\nWard A,general,3\n")
    future = datetime.date.today() + datetime.timedelta(days=300)
    (tmp_path / "opening_stock.csv").write_text(
        f"generic_name,strength,form,batch_number,expiry_date,quantity,unit_cost\nParacetamol,500mg,Tablet,B1,{future},100,2\n"
    )
    call_command("load_setup", str(tmp_path), "--credentials-out", str(tmp_path / "c.csv"), verbosity=0)
    bad = [(label, detail) for ok, label, detail in readiness.run() if not ok]
    assert bad == []
    call_command("check_setup", verbosity=0)
