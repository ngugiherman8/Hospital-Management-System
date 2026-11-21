import datetime
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role
from apps.hr import services as hr
from apps.hr.models import Contract, Department, LeaveType, StaffProfile
from apps.payroll import services
from apps.payroll.models import Adjustment, Allowance, Deduction, PayRun, Payslip, TaxBand
from conftest import make_user

PERIOD = timezone.localdate().replace(day=1)


@pytest.fixture
def people(db):
    return make_user(Role.HR, username="hr"), make_user(Role.MANAGER, username="boss")


@pytest.fixture
def rates(people):
    call_command("seed_payroll")
    services.confirm_rates(people[1])


@pytest.fixture
def dept(db):
    return Department.objects.create(name="Ward")


def staff(dept, salary=60000, first="Amina", joined=None, **kw):
    s = StaffProfile.objects.create(
        first_name=first, last_name="Test", department=dept, job_title="Nurse", cadre="nursing",
        date_joined=joined or PERIOD - datetime.timedelta(days=400), **kw,
    )  # fmt: skip
    Contract.objects.create(staff=s, kind="permanent", start_date=s.date_joined, basic_salary=salary)
    return s


def run_for(people, period=PERIOD):
    run = services.create_run(period, people[0])
    services.calculate(run, people[0])
    return run


def test_paye_for_a_known_salary(dept, people, rates):
    """KES 60,000 basic: worked by hand from the seeded figures."""
    s = staff(dept, 60000)
    run = run_for(people)
    p = Payslip.objects.get(run=run, staff=s)
    # NSSF 6% of 60,000 = 3,600. SHIF 2.75% = 1,650. Housing 1.5% = 900.
    assert (p.nssf, p.shif, p.housing_levy) == (D("3600.00"), D("1650.00"), D("900.00"))
    # Taxable = 60,000 - 3,600 - 1,650 - 900 = 53,850.
    assert p.taxable_pay == D("53850.00")
    # Tax = 24,000*10% + 8,333*25% + (53,850-32,333)*30% = 2,400 + 2,083.25 + 6,455.10 = 10,938.35
    assert p.tax_before_relief == D("10938.35")
    assert p.paye == D("8538.35") and p.relief == D("2400.00")
    assert p.net == D("60000") - D("3600") - D("1650") - D("900") - D("8538.35")


def test_nssf_is_capped_at_the_upper_limit(dept, people, rates):
    s = staff(dept, 200000)
    p = Payslip.objects.get(run=run_for(people), staff=s)
    assert p.nssf == D("6480.00") and p.employer_nssf == D("6480.00")


def test_shif_has_a_minimum(dept, people, rates):
    s = staff(dept, 5000)
    assert Payslip.objects.get(run=run_for(people), staff=s).shif == D("300.00")


def test_low_pay_pays_no_paye_after_relief(dept, people, rates):
    s = staff(dept, 20000)
    assert Payslip.objects.get(run=run_for(people), staff=s).paye == D("0.00")


def test_nothing_runs_until_figures_are_confirmed(dept, people):
    call_command("seed_payroll")
    staff(dept)
    run = services.create_run(PERIOD, people[0])
    with pytest.raises(ValidationError) as e:
        services.calculate(run, people[0])
    assert any("Not yet confirmed" in m for m in e.value.messages)
    services.confirm_rates(people[1])
    assert services.calculate(run, people[0]) == 1


def test_a_new_rate_must_be_confirmed_before_it_applies(dept, people, rates):
    from apps.payroll.models import PayrollParameter

    PayrollParameter.objects.create(key="personal_relief", value=3000, effective_from=PERIOD)
    run = services.create_run(PERIOD, people[0])
    with pytest.raises(ValidationError, match="Not yet confirmed"):
        services.calculate(run, people[0])


def test_allowances_deductions_and_one_offs(dept, people, rates):
    s = staff(dept, 60000)
    Allowance.objects.create(staff=s, name="House", amount=10000, taxable=True, start_date=PERIOD)
    Allowance.objects.create(staff=s, name="Lunch", amount=2000, taxable=False, start_date=PERIOD)
    Deduction.objects.create(staff=s, name="Sacco", amount=3000, start_date=PERIOD)
    Adjustment.objects.create(staff=s, period=PERIOD, kind="earning", description="Overtime", amount=1000)
    Adjustment.objects.create(staff=s, period=PERIOD, kind="deduction", description="Fine", amount=500)
    p = Payslip.objects.get(run=run_for(people), staff=s)
    assert p.gross == D("73000.00") and p.other_deductions == D("3500.00")
    assert p.net == p.gross - p.nssf - p.shif - p.housing_levy - p.paye - D("3500.00")
    labels = [ln.label for ln in p.lines.all()]
    assert {"Basic pay", "House", "Lunch", "Overtime", "Sacco", "Fine", "PAYE"} <= set(labels)


def test_a_loan_stops_when_it_is_paid_back(dept, people, rates):
    s = staff(dept)
    d = Deduction.objects.create(staff=s, kind="loan", name="Loan", amount=3000, total_owed=4000, start_date=PERIOD)
    run = run_for(people)
    approver = people[1]
    services.approve(run, approver)
    assert services.repaid(d) == D("3000.00")
    nxt = (PERIOD + datetime.timedelta(days=32)).replace(day=1)
    compute = services.compute_payslip
    run2 = PayRun.objects.create(period=nxt, prepared_by=people[0])
    rts, bands, _, _ = services.load_rates(services.month_end(nxt))
    fields, lines = compute(s, run2, rts, bands)
    assert fields["other_deductions"] == D("1000.00")  # only what is left


def test_half_a_month_for_someone_who_joined_mid_month(dept, people, rates):
    joined = PERIOD.replace(day=16)
    s = staff(dept, 31000, joined=joined)
    days = services.month_end(PERIOD).day
    p = Payslip.objects.get(run=run_for(people), staff=s)
    assert p.basic == (D(31000) * D(days - 15) / D(days)).quantize(D("0.01"))


def test_unpaid_leave_is_taken_off_basic(dept, people, rates):
    s = staff(dept, 26000)
    unpaid = LeaveType.objects.create(name="Unpaid", uses_balance=False, is_paid=False)
    first = PERIOD
    while first.weekday() == 6:
        first += datetime.timedelta(days=1)
    req = hr.request_leave(s, unpaid, first, first, "", people[0])
    hr.decide_leave(req, True, people[1])
    month_days = hr.working_days(PERIOD, services.month_end(PERIOD))
    p = Payslip.objects.get(run=run_for(people), staff=s)
    assert p.basic == (D(26000) * (1 - D(1) / month_days)).quantize(D("0.01"))


def test_the_preparer_cannot_approve_and_approval_locks_everything(dept, people, rates):
    s = staff(dept)
    run = run_for(people)
    with pytest.raises(ValidationError, match="cannot approve"):
        services.approve(run, people[0])
    services.approve(run, people[1])
    slip = Payslip.objects.get(run=run, staff=s)
    slip.net = 1
    with pytest.raises(PermissionError):
        slip.save()
    with pytest.raises(PermissionError):
        slip.delete()
    with pytest.raises(PermissionError):
        slip.lines.first().save()
    with pytest.raises(ValidationError, match="approved"):
        services.calculate(run, people[0])


def test_negative_pay_blocks_approval(dept, people, rates):
    s = staff(dept, 20000)
    Adjustment.objects.create(staff=s, period=PERIOD, kind="deduction", description="Big", amount=50000)
    run = run_for(people)
    with pytest.raises(ValidationError, match="more than pay"):
        services.approve(run, people[1])


def test_only_one_run_a_month_and_not_in_the_future(people):
    services.create_run(PERIOD, people[0])
    with pytest.raises(ValidationError, match="already exists"):
        services.create_run(PERIOD, people[0])
    with pytest.raises(ValidationError, match="not started"):
        services.create_run((PERIOD + datetime.timedelta(days=40)).replace(day=1), people[0])


def test_people_who_left_before_the_month_are_not_paid(dept, people, rates):
    staff(dept, joined=PERIOD - datetime.timedelta(days=300), date_left=PERIOD - datetime.timedelta(days=5))
    live = staff(dept, first="Live")
    run = run_for(people)
    assert list(run.payslips.values_list("staff", flat=True)) == [live.pk]


def test_exports_need_an_approved_run(dept, people, rates):
    staff(dept, bank_name="KCB", bank_account="123")
    run = run_for(people)
    c = Client()
    c.force_login(people[1])
    assert c.get(f"/payroll/runs/{run.pk}/export/bank/").status_code == 302
    services.approve(run, people[1])
    body = c.get(f"/payroll/runs/{run.pk}/export/bank/").content.decode()
    assert "KCB" in body and "123" in body
    assert "PAYE" in c.get(f"/payroll/runs/{run.pk}/export/statutory/").content.decode()
    assert c.get(f"/payroll/runs/{run.pk}/export/other/").status_code == 404


def test_staff_see_only_their_own_approved_payslips(dept, people, rates):
    u1, u2 = make_user(Role.NURSE, username="n1"), make_user(Role.NURSE, username="n2")
    s1 = staff(dept, user=u1)
    staff(dept, first="Other", user=u2)
    run = run_for(people)
    p1 = Payslip.objects.get(run=run, staff=s1)
    c1, c2 = Client(), Client()
    c1.force_login(u1)
    c2.force_login(u2)
    assert c1.get(f"/payroll/payslips/{p1.pk}/").status_code == 403  # not approved yet
    services.approve(run, people[1])
    assert c1.get(f"/payroll/payslips/{p1.pk}/").status_code == 200
    assert c2.get(f"/payroll/payslips/{p1.pk}/").status_code == 403
    assert str(p1.net) in c1.get("/payroll/mine/").content.decode()


def test_who_may_do_what(dept, people, rates):
    s = staff(dept)
    run = run_for(people)
    hr_c, boss, nurse = Client(), Client(), Client()
    hr_c.force_login(people[0])
    boss.force_login(people[1])
    nurse.force_login(make_user(Role.NURSE, username="nn"))
    assert nurse.get("/payroll/").status_code == 403
    assert nurse.get("/payroll/rates/").status_code == 403
    assert nurse.get(f"/payroll/staff/{s.pk}/").status_code == 403
    assert boss.get(f"/payroll/staff/{s.pk}/").status_code == 403  # a manager does not edit pay
    assert hr_c.get(f"/payroll/staff/{s.pk}/").status_code == 200
    assert hr_c.post(f"/payroll/runs/{run.pk}/approve/").status_code == 403  # preparers cannot approve
    assert hr_c.post("/payroll/rates/confirm/").status_code == 403
    assert boss.post(f"/payroll/runs/{run.pk}/calculate/").status_code == 403
    assert boss.post(f"/payroll/runs/{run.pk}/approve/").status_code == 302
    assert PayRun.objects.get().status == "approved"


def test_the_bands_must_start_at_zero(dept, people):
    call_command("seed_payroll")
    TaxBand.objects.filter(lower=0).update(lower=1)
    services.confirm_rates(people[1])
    staff(dept)
    run = services.create_run(PERIOD, people[0])
    with pytest.raises(ValidationError, match="start at 0"):
        services.calculate(run, people[0])
