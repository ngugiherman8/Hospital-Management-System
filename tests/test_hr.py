import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role
from apps.hr import services
from apps.hr.models import (
    Contract,
    Department,
    LeaveEntry,
    LeaveRequest,
    LeaveType,
    Licence,
    PublicHoliday,
    StaffProfile,
)
from conftest import make_user

TODAY = timezone.localdate()


def d(n):
    return TODAY + datetime.timedelta(days=n)


@pytest.fixture
def dept(db):
    return Department.objects.create(name="Ward")


def make_staff(dept, first="Amina", last="Otieno", user=None, **kw):
    kw.setdefault("job_title", "Nurse")
    kw.setdefault("cadre", "nursing")
    kw.setdefault("date_joined", d(-400))
    return StaffProfile.objects.create(first_name=first, last_name=last, department=dept, user=user, **kw)


@pytest.fixture
def annual(db):
    return LeaveType.objects.create(name="Annual", annual_days=21)


def monday():
    day = d(30)
    return day - datetime.timedelta(days=day.weekday())  # a Monday in the future


def test_staff_numbers_are_in_sequence(dept):
    a, b = make_staff(dept), make_staff(dept, "Brian", "Kamau")
    assert (a.staff_number, b.staff_number) == ("S-00001", "S-00002")


def test_the_bank_account_is_kept_out_of_the_audit_log(dept):
    from apps.core.models import AuditLog

    make_staff(dept, bank_account="0123456789")
    assert "0123456789" not in str(list(AuditLog.objects.values_list("changes", flat=True)))


def test_current_contract_picks_the_one_in_force(dept):
    s = make_staff(dept)
    Contract.objects.create(staff=s, kind="fixed", start_date=d(-300), end_date=d(-1), basic_salary=50000)
    new = Contract.objects.create(staff=s, kind="permanent", start_date=d(0), basic_salary=60000)
    assert s.contract_on() == new
    assert s.contract_on(d(-10)).basic_salary == 50000


def test_leave_days_skip_sundays_and_holidays(db):
    m = monday()
    assert services.working_days(m, m + datetime.timedelta(days=6)) == 6
    PublicHoliday.objects.create(date=m + datetime.timedelta(days=2), name="Test day")
    assert services.working_days(m, m + datetime.timedelta(days=6)) == 5


def test_licence_status_and_blocks(dept):
    s = make_staff(dept)
    Licence.objects.create(staff=s, body="nck", number="1", expires_on=d(-5))
    assert s.licences.get().status() == "expired"
    assert services.licence_blocks(s, d(0)) == ["Nursing Council of Kenya"]
    Licence.objects.create(staff=s, body="nck", number="2", expires_on=d(300))  # renewed
    assert services.licence_blocks(s, d(0)) == []


def test_leave_request_balance_and_approval(dept, annual):
    boss = make_user(Role.MANAGER)
    s = make_staff(dept)
    services.accrue_leave(TODAY.replace(day=1), boss)
    assert services.balance(s, annual) == Decimal("1.75")
    m = monday()
    with pytest.raises(ValidationError, match="free in the balance"):
        services.request_leave(s, annual, m, m + datetime.timedelta(days=4), "", boss)
    services.adjust_leave(s, annual, Decimal("10"), "Brought forward", boss)
    req = services.request_leave(s, annual, m, m + datetime.timedelta(days=2), "Family", boss)
    assert req.days == 3
    with pytest.raises(ValidationError, match="overlaps"):
        services.request_leave(s, annual, m, m, "", boss)
    req, _ = services.decide_leave(req, True, boss)
    assert req.status == "approved" and services.balance(s, annual) == Decimal("8.75")
    services.cancel_leave(req, boss)
    assert services.balance(s, annual) == Decimal("11.75")
    assert LeaveEntry.objects.filter(request=req).count() == 2  # taken then put back, nothing edited


def test_nobody_decides_their_own_leave_and_rejection_needs_a_reason(dept, annual):
    boss = make_user(Role.MANAGER, username="boss")
    mine = make_staff(dept, user=boss)
    services.adjust_leave(mine, annual, Decimal("5"), "Start", boss)
    m = monday()
    req = services.request_leave(mine, annual, m, m, "", boss)
    with pytest.raises(ValidationError, match="own leave"):
        services.decide_leave(req, True, boss)
    other = make_user(Role.HR, username="hr1")
    with pytest.raises(ValidationError, match="why"):
        services.decide_leave(req, False, other)
    assert services.decide_leave(req, False, other, "Busy week")[0].status == "rejected"


def test_a_month_of_leave_is_added_once(dept, annual):
    make_staff(dept)
    hr = make_user(Role.HR)
    first = services.accrue_leave(TODAY.replace(day=1), hr)
    assert first == 1 and services.accrue_leave(TODAY.replace(day=1), hr) == 0


def test_leave_ledger_cannot_be_edited(dept, annual):
    s = make_staff(dept)
    e = services.adjust_leave(s, annual, Decimal("2"), "x", make_user(Role.HR))
    e.days = 50
    with pytest.raises(PermissionError):
        e.save()


def login(role, **kw):
    c = Client()
    c.force_login(make_user(role, username=f"u-{role.value}", **kw))
    return c


def test_hr_sees_pay_details_and_a_manager_does_not(dept):
    s = make_staff(dept, kra_pin="A001", bank_account="9988")
    Contract.objects.create(staff=s, kind="permanent", start_date=d(-5), basic_salary=77777)
    hr, mgr = login(Role.HR), login(Role.MANAGER)
    full = hr.get(f"/hr/{s.pk}/").content.decode()
    assert "77777" in full and "A001" in full
    limited = mgr.get(f"/hr/{s.pk}/").content.decode()
    assert "77777" not in limited and "A001" not in limited and "9988" not in limited
    assert mgr.get("/hr/new/").status_code == 403
    assert mgr.post(f"/hr/{s.pk}/contracts/new/", {}).status_code == 403


@pytest.mark.parametrize("role", [Role.NURSE, Role.DOCTOR, Role.CASHIER, Role.RECEPTION, Role.STOREKEEPER])
def test_other_staff_cannot_open_the_staff_records(dept, role):
    c = login(role)
    for path in ["/hr/", "/hr/licences/", "/hr/leave/approvals/", "/hr/leave/balances/", "/hr/holidays/"]:
        assert c.get(path).status_code == 403, path


def test_overlapping_contracts_are_refused_by_the_page(dept):
    s = make_staff(dept)
    hr = login(Role.HR)
    body = {"kind": "permanent", "start_date": str(d(-10)), "basic_salary": "50000", "hours_per_week": 45}
    hr.post(f"/hr/{s.pk}/contracts/new/", body)
    hr.post(f"/hr/{s.pk}/contracts/new/", {**body, "start_date": str(d(-2))})
    assert s.contracts.count() == 1


def test_my_leave_page_needs_a_staff_record_but_never_fails(dept, annual):
    nurse = login(Role.NURSE)
    assert b"not linked" in nurse.get("/hr/leave/").content
    user = make_user(Role.DOCTOR, username="linked")
    make_staff(dept, user=user)
    c = Client()
    c.force_login(user)
    assert c.get("/hr/leave/").status_code == 200


def test_a_staff_member_can_cancel_only_their_own_leave(dept, annual):
    owner = make_user(Role.NURSE, username="owner")
    s = make_staff(dept, user=owner)
    services.adjust_leave(s, annual, Decimal("5"), "x", make_user(Role.HR))
    m = monday()
    req = services.request_leave(s, annual, m, m, "", owner)
    other = login(Role.NURSE)
    assert other.post(f"/hr/leave/{req.pk}/cancel/").status_code == 403
    mine = Client()
    mine.force_login(owner)
    mine.post(f"/hr/leave/{req.pk}/cancel/")
    assert LeaveRequest.objects.get(pk=req.pk).status == "cancelled"


def test_seed_hr_is_safe_to_run_twice(db):
    from django.core.management import call_command

    call_command("seed_hr")
    n = LeaveType.objects.count()
    call_command("seed_hr")
    assert LeaveType.objects.count() == n and Department.objects.count() >= 8
