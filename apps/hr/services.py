import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import LICENCE_WARNING_DAYS, LeaveEntry, LeaveRequest, LeaveType, Licence, PublicHoliday, StaffProfile
from .signals import leave_approved

TWO = Decimal("0.01")


def working_days(start, end):
    """Days from start to end that count as leave: Monday to Saturday, less public holidays."""
    holidays = set(PublicHoliday.objects.filter(date__gte=start, date__lte=end).values_list("date", flat=True))
    n, day = 0, start
    while day <= end:
        if day.weekday() != 6 and day not in holidays:
            n += 1
        day += datetime.timedelta(days=1)
    return Decimal(n)


def balance(staff, leave_type):
    total = LeaveEntry.objects.filter(staff=staff, leave_type=leave_type).aggregate(t=Sum("days"))["t"]
    return total or Decimal("0")


def pending_days(staff, leave_type):
    total = LeaveRequest.objects.filter(
        staff=staff, leave_type=leave_type, status=LeaveRequest.Status.REQUESTED
    ).aggregate(t=Sum("days"))["t"]
    return total or Decimal("0")


def balances_for(staff):
    rows = []
    for lt in LeaveType.objects.filter(is_active=True, uses_balance=True):
        rows.append({"type": lt, "balance": balance(staff, lt), "pending": pending_days(staff, lt)})
    return rows


def request_leave(staff, leave_type, start, end, reason, user):
    if end < start:
        raise ValidationError("The last day cannot be before the first day.")
    if not leave_type.is_active:
        raise ValidationError("That kind of leave is not in use.")
    if not staff.is_active_on(start):
        raise ValidationError("This person is not on the staff list for those dates.")
    days = working_days(start, end)
    if days <= 0:
        raise ValidationError("Those dates are only Sundays and public holidays, so there is nothing to take.")
    with transaction.atomic():
        StaffProfile.objects.select_for_update().get(pk=staff.pk)
        clash = (
            LeaveRequest.objects.filter(
                staff=staff,
                status__in=[LeaveRequest.Status.REQUESTED, LeaveRequest.Status.APPROVED],
                start_date__lte=end,
                end_date__gte=start,
            )
            .select_related("leave_type")
            .first()
        )
        if clash:
            raise ValidationError(f"This overlaps leave already asked for ({clash.start_date} to {clash.end_date}).")
        if leave_type.uses_balance:
            free = balance(staff, leave_type) - pending_days(staff, leave_type)
            if days > free:
                raise ValidationError(f"{days} days are asked for but only {free} are free in the balance.")
        return LeaveRequest.objects.create(
            staff=staff,
            leave_type=leave_type,
            start_date=start,
            end_date=end,
            days=days,
            reason=reason,
            requested_by=user,
        )


def decide_leave(leave, approve, user, note=""):
    """Approve or reject. Nobody decides their own leave. Approval takes the days off the balance."""
    released = 0
    with transaction.atomic():
        leave = LeaveRequest.objects.select_for_update().select_related("staff", "leave_type").get(pk=leave.pk)
        if leave.status != LeaveRequest.Status.REQUESTED:
            raise ValidationError("This request has already been dealt with.")
        if leave.staff.user_id and leave.staff.user_id == user.pk:
            raise ValidationError("You cannot decide your own leave. Ask someone else.")
        if not approve and not note.strip():
            raise ValidationError("Say why the leave is rejected.")
        if approve and leave.leave_type.uses_balance and balance(leave.staff, leave.leave_type) < leave.days:
            raise ValidationError("The balance is no longer enough for these days.")
        leave.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
        leave.decided_by, leave.decided_at, leave.decision_note = user, timezone.now(), note
        leave.save()
        if approve:
            if leave.leave_type.uses_balance:
                LeaveEntry.objects.create(
                    staff=leave.staff,
                    leave_type=leave.leave_type,
                    kind=LeaveEntry.Kind.TAKEN,
                    days=-leave.days,
                    request=leave,
                    note=f"{leave.start_date} to {leave.end_date}",
                    user=user,
                )
            for _, count in leave_approved.send(sender=LeaveRequest, leave=leave, user=user):
                released += count or 0
    return leave, released


def cancel_leave(leave, user):
    with transaction.atomic():
        leave = LeaveRequest.objects.select_for_update().select_related("staff", "leave_type").get(pk=leave.pk)
        if leave.status not in (LeaveRequest.Status.REQUESTED, LeaveRequest.Status.APPROVED):
            raise ValidationError("Only leave that is waiting or approved can be cancelled.")
        if leave.start_date <= timezone.localdate() and leave.status == LeaveRequest.Status.APPROVED:
            raise ValidationError("This leave has started. Ask HR to fix the balance by hand.")
        was_approved = leave.status == LeaveRequest.Status.APPROVED
        leave.status = LeaveRequest.Status.CANCELLED
        leave.save()
        if was_approved and leave.leave_type.uses_balance:
            LeaveEntry.objects.create(
                staff=leave.staff,
                leave_type=leave.leave_type,
                kind=LeaveEntry.Kind.REVERSAL,
                days=leave.days,
                request=leave,
                note="Leave cancelled",
                user=user,
            )
    return leave


def adjust_leave(staff, leave_type, days, note, user):
    if not days:
        raise ValidationError("The adjustment cannot be zero.")
    if not note.strip():
        raise ValidationError("Say why the balance is being adjusted.")
    return LeaveEntry.objects.create(
        staff=staff, leave_type=leave_type, kind=LeaveEntry.Kind.ADJUST, days=days, note=note, user=user
    )


def accrue_leave(month_start, user):
    """Add one month of entitlement for everyone on the staff list. Safe to run twice for the same month."""
    month_end = (month_start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    added = 0
    types = list(LeaveType.objects.filter(is_active=True, uses_balance=True, annual_days__gt=0))
    for staff in StaffProfile.objects.filter(date_joined__lte=month_end).filter(
        Q(date_left__isnull=True) | Q(date_left__gte=month_start)
    ):
        for lt in types:
            key = f"accrue:{staff.pk}:{lt.pk}:{month_start:%Y-%m}"
            try:
                with transaction.atomic():
                    LeaveEntry.objects.create(
                        staff=staff,
                        leave_type=lt,
                        kind=LeaveEntry.Kind.ACCRUAL,
                        days=(lt.annual_days / 12).quantize(TWO),
                        source_key=key,
                        note=f"{month_start:%B %Y}",
                        user=user,
                    )
                added += 1
            except IntegrityError:
                pass
    return added


def unpaid_days_in(staff, start, end):
    """Approved unpaid leave days that fall inside a pay period."""
    total = Decimal("0")
    for lv in LeaveRequest.objects.filter(
        staff=staff,
        status=LeaveRequest.Status.APPROVED,
        leave_type__is_paid=False,
        start_date__lte=end,
        end_date__gte=start,
    ):
        total += working_days(max(lv.start_date, start), min(lv.end_date, end))
    return total


def licence_problems(today=None):
    """Current licences that have run out or will soon, for people still on the staff list."""
    today = today or timezone.localdate()
    soon = today + datetime.timedelta(days=LICENCE_WARNING_DAYS)
    return (
        Licence.objects.filter(expires_on__lte=soon)
        .filter(Q(staff__date_left__isnull=True) | Q(staff__date_left__gte=today))
        .select_related("staff", "staff__department")
        .order_by("expires_on")
    )


def licence_blocks(staff, day):
    """Names of professional bodies whose newest licence for this person has expired by `day`."""
    newest = {}
    for lic in staff.licences.all():
        if lic.body not in newest or lic.expires_on > newest[lic.body].expires_on:
            newest[lic.body] = lic
    return [lic.get_body_display() for lic in newest.values() if lic.expires_on < day]
