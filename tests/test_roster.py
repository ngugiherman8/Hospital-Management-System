import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role
from apps.hr import services as hr
from apps.hr.models import Contract, Department, LeaveType, Licence, StaffProfile
from apps.roster import services
from apps.roster.models import Coverage, Punch, Shift, ShiftSwap, ShiftType
from conftest import make_user

T = datetime.time


def next_monday():
    today = timezone.localdate()
    return today + datetime.timedelta(days=7 - today.weekday())


MON = next_monday()


def day(n):
    return MON + datetime.timedelta(days=n)


@pytest.fixture
def setup(db):
    dept = Department.objects.create(name="Ward")
    morning = ShiftType.objects.create(name="Morning", code="M", start_time=T(7), end_time=T(15))
    night = ShiftType.objects.create(name="Night", code="N", start_time=T(21), end_time=T(7))
    planner = make_user(Role.MANAGER, username="planner")
    return dept, morning, night, planner


def staff(dept, first="Amina", user=None):
    s = StaffProfile.objects.create(
        first_name=first,
        last_name="Test",
        department=dept,
        job_title="Nurse",
        cadre="nursing",
        date_joined=MON - datetime.timedelta(days=500),
        user=user,
    )
    Contract.objects.create(staff=s, kind="permanent", start_date=s.date_joined, basic_salary=50000, hours_per_week=45)
    return s


def test_shift_type_hours_and_midnight(setup):
    _, morning, night, _ = setup
    assert morning.hours == 8 and not morning.crosses_midnight
    assert night.hours == 10 and night.crosses_midnight


def test_a_night_shift_ends_the_next_morning(setup):
    dept, _, night, planner = setup
    shift, _ = services.assign(staff(dept), dept, night, day(0), planner)
    assert shift.ends_at.date() == day(1) and shift.ends_at.hour == 7


def test_overlapping_shifts_are_refused(setup):
    dept, morning, _, planner = setup
    s = staff(dept)
    long_day = ShiftType.objects.create(name="Long", code="L", start_time=T(7), end_time=T(19))
    services.assign(s, dept, morning, day(0), planner)
    with pytest.raises(ValidationError, match="overlaps"):
        services.assign(s, dept, long_day, day(0), planner)


def test_not_enough_rest_after_a_night_is_refused(setup):
    dept, morning, night, planner = setup
    s = staff(dept)
    services.assign(s, dept, night, day(0), planner)  # ends 07:00 on day 1
    with pytest.raises(ValidationError, match="rest"):
        services.assign(s, dept, morning, day(1), planner)
    services.assign(s, dept, morning, day(2), planner)  # a full day later is fine


def test_the_same_shift_twice_is_refused(setup):
    dept, morning, _, planner = setup
    s = staff(dept)
    services.assign(s, dept, morning, day(0), planner)
    with pytest.raises(ValidationError):
        services.assign(s, dept, morning, day(0), planner)


def test_leave_blocks_a_shift_and_approval_clears_planned_ones(setup):
    dept, morning, _, planner = setup
    s = staff(dept)
    annual = LeaveType.objects.create(name="Annual", annual_days=21)
    hr.adjust_leave(s, annual, Decimal("10"), "start", planner)
    services.assign(s, dept, morning, day(1), planner)
    req = hr.request_leave(s, annual, day(0), day(2), "", planner)
    approver = make_user(Role.HR, username="hr")
    _, released = hr.decide_leave(req, True, approver)
    assert released == 1 and Shift.objects.get().status == "cancelled"
    with pytest.raises(ValidationError, match="on Annual"):
        services.assign(s, dept, morning, day(1), planner)


def test_an_expired_licence_blocks_a_shift(setup):
    dept, morning, _, planner = setup
    s = staff(dept)
    Licence.objects.create(staff=s, body="nck", number="1", expires_on=day(0) - datetime.timedelta(days=1))
    with pytest.raises(ValidationError, match="licence has expired"):
        services.assign(s, dept, morning, day(0), planner)


def test_weekly_hours_over_the_contract_only_warn(setup):
    dept, morning, _, planner = setup
    s = staff(dept)
    warned = []
    for i in range(6):
        _, w = services.assign(s, dept, morning, day(i), planner)
        warned += w
    assert warned and "hours this week" in warned[0]


def test_cover_flags_a_short_day(setup):
    dept, morning, _, planner = setup
    Coverage.objects.create(department=dept, shift_type=morning, minimum=2)
    services.assign(staff(dept), dept, morning, day(0), planner)
    services.assign(staff(dept, "Brian"), dept, morning, day(0), planner)
    services.assign(staff(dept, "Chao"), dept, morning, day(1), planner)
    cells = services.grid(dept, MON)["cover"][0]["cells"]
    assert [c["short"] for c in cells[:3]] == [False, True, True]


def test_copy_week_skips_what_would_break_a_rule(setup):
    dept, morning, _, planner = setup
    s = staff(dept)
    services.assign(s, dept, morning, day(0), planner)
    services.assign(s, dept, morning, day(1), planner)
    annual = LeaveType.objects.create(name="Annual", annual_days=21)
    hr.adjust_leave(s, annual, Decimal("5"), "x", planner)
    req = hr.request_leave(s, annual, day(8), day(8), "", planner)
    hr.decide_leave(req, True, make_user(Role.HR, username="hr"))
    made, skipped = services.copy_week(dept, MON, MON + datetime.timedelta(days=7), planner)
    assert made == 1 and len(skipped) == 1


def test_swap_needs_the_colleague_then_the_planner(setup):
    dept, morning, _, planner = setup
    u1, u2 = make_user(Role.NURSE, username="a"), make_user(Role.NURSE, username="b")
    a, b = staff(dept, "A", u1), staff(dept, "B", u2)
    shift, _ = services.assign(a, dept, morning, day(3), planner)
    swap = services.propose_swap(shift, b, u1)
    with pytest.raises(ValidationError):
        services.respond_swap(swap, "approve", planner)  # colleague has not agreed yet
    with pytest.raises(ValidationError):
        services.respond_swap(swap, "accept", u1)  # not the colleague
    services.respond_swap(swap, "accept", u2)
    with pytest.raises(ValidationError):
        services.respond_swap(swap, "approve", u1)  # not a planner
    services.respond_swap(swap, "approve", planner)
    shift.refresh_from_db()
    assert shift.staff == b and swap.__class__.objects.get().status == ShiftSwap.Status.APPROVED


def test_swap_is_rechecked_on_approval(setup):
    dept, morning, _, planner = setup
    u1, u2 = make_user(Role.NURSE, username="a"), make_user(Role.NURSE, username="b")
    a, b = staff(dept, "A", u1), staff(dept, "B", u2)
    shift, _ = services.assign(a, dept, morning, day(3), planner)
    swap = services.propose_swap(shift, b, u1)
    services.respond_swap(swap, "accept", u2)
    Licence.objects.create(staff=b, body="nck", number="1", expires_on=day(0))  # runs out before the shift
    with pytest.raises(ValidationError, match="licence"):
        services.respond_swap(swap, "approve", planner)


def test_clocking_in_and_out(setup):
    dept, *_ = setup
    u = make_user(Role.NURSE, username="n")
    s = staff(dept, user=u)
    with pytest.raises(ValidationError, match="not clocked in"):
        services.clock(s, "out", u)
    services.clock(s, "in", u)
    with pytest.raises(ValidationError, match="already"):
        services.clock(s, "in", u)
    services.clock(s, "out", u)
    assert s.punches.count() == 2
    with pytest.raises(ValidationError, match="only clock yourself"):
        services.clock(s, "in", make_user(Role.NURSE, username="other"))


def test_clocking_can_be_limited_to_the_hospital_network(setup, settings):
    dept, *_ = setup
    settings.ATTENDANCE_ALLOWED_NETS = ["10.0.0.0/24"]
    u = make_user(Role.NURSE, username="n")
    s = staff(dept, user=u)
    with pytest.raises(ValidationError, match="hospital network"):
        services.clock(s, "in", u, "8.8.8.8")
    services.clock(s, "in", u, "10.0.0.7")


def test_punches_cannot_be_edited_and_missed_ones_need_a_reason(setup):
    dept, _, _, planner = setup
    s = staff(dept)
    with pytest.raises(ValidationError):
        services.add_missed_punch(s, "in", timezone.now(), " ", planner)
    p = services.add_missed_punch(s, "in", timezone.now() - datetime.timedelta(hours=2), "Forgot", planner)
    assert p.source == "supervisor"
    p.note = "changed"
    with pytest.raises(PermissionError):
        p.save()


def test_attendance_counts_late_absent_and_hours(setup, monkeypatch):
    dept, morning, _, planner = setup
    s1, s2 = staff(dept, "Early"), staff(dept, "Absent")
    past = timezone.localdate() - datetime.timedelta(days=3)
    for s in (s1, s2):
        Shift.objects.create(staff=s, department=dept, shift_type=morning, date=past)
    sh = Shift.objects.filter(staff=s1).get()
    Punch.objects.create(staff=s1, kind="in", at=sh.starts_at + datetime.timedelta(minutes=30))
    Punch.objects.create(staff=s1, kind="out", at=sh.ends_at)
    rep = {r["staff"].first_name: r for r in services.attendance_report(dept, past, past)}
    assert rep["Early"]["attended"] == 1 and rep["Early"]["late"] == 1 and rep["Early"]["hours"] == 7.5
    assert rep["Absent"]["absent"] == 1


def test_pages_and_permissions(setup):
    dept, morning, _, planner = setup
    nurse_user = make_user(Role.NURSE, username="n")
    s = staff(dept, user=nurse_user)
    nurse = Client()
    nurse.force_login(nurse_user)
    boss = Client()
    boss.force_login(planner)
    assert nurse.get("/roster/").status_code == 200  # everyone can see the roster
    assert nurse.get("/roster/mine/").status_code == 200
    assert nurse.post(f"/roster/department/{dept.pk}/add/", {}).status_code == 403
    assert nurse.get("/roster/attendance/").status_code == 403
    assert nurse.get("/roster/setup/").status_code == 403
    assert boss.get("/roster/setup/").status_code == 403  # set-up belongs to HR and administrators
    r = boss.post(
        f"/roster/department/{dept.pk}/add/",
        {"staff": s.pk, "shift_type": morning.pk, "date": str(day(0)), "week": str(MON)},
    )
    assert r.status_code == 302 and Shift.objects.count() == 1
    assert b"M" in boss.get(f"/roster/?dept={dept.pk}&week={MON}").content
    assert nurse.post("/roster/clock/", {"kind": "in"}).status_code == 302
    assert Punch.objects.count() == 1


def test_seed_roster_is_safe_twice(db):
    from django.core.management import call_command

    call_command("seed_roster")
    call_command("seed_roster")
    assert ShiftType.objects.count() == 4
