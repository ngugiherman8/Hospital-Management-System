import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core.audit import log_access
from apps.core.permissions import role_required

from . import services
from .forms import (
    AdjustLeaveForm,
    ContractForm,
    DepartmentForm,
    HolidayForm,
    LeaveRequestForm,
    LeaveTypeForm,
    LicenceForm,
    StaffForm,
)
from .models import Contract, Department, LeaveRequest, LeaveType, PublicHoliday, StaffProfile

HR = (Role.HR,)  # administrators always pass
HR_VIEW = (Role.HR, Role.MANAGER)
EVERYONE = tuple(Role)


def _errors(request, exc):
    messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


def own_profile(user):
    return StaffProfile.objects.filter(user=user).select_related("department").first()


# ---------- staff records ----------


@role_required(*HR_VIEW)
def staff_list(request):
    q = request.GET.get("q", "").strip()
    show = request.GET.get("show", "current")
    rows = StaffProfile.objects.select_related("department")
    if q:
        rows = rows.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(staff_number__icontains=q)
            | Q(job_title__icontains=q)
        )
    today = timezone.localdate()
    if show == "current":
        rows = rows.filter(Q(date_left__isnull=True) | Q(date_left__gte=today))
    elif show == "left":
        rows = rows.filter(date_left__lt=today)
    return render(
        request,
        "hr/staff_list.html",
        {"rows": rows, "q": q, "show": show, "can_edit": request.user.has_role(*HR)},
    )


@role_required(*HR)
def staff_create(request):
    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        staff = form.save()
        messages.success(request, f"{staff.full_name} added as {staff.staff_number}.")
        return redirect("staff_detail", pk=staff.pk)
    return render(request, "hr/staff_form.html", {"form": form, "title": "Add a staff member"})


@role_required(*HR_VIEW)
def staff_detail(request, pk):
    staff = get_object_or_404(StaffProfile.objects.select_related("department", "user"), pk=pk)
    log_access(staff)
    full = request.user.has_role(*HR)
    return render(
        request,
        "hr/staff_detail.html",
        {
            "staff": staff,
            "full": full,
            "contract": staff.contract_on() if full else None,
            "contracts": staff.contracts.all() if full else [],
            "licences": staff.licences.all(),
            "balances": services.balances_for(staff),
            "contract_form": ContractForm(),
            "licence_form": LicenceForm(),
        },
    )


@role_required(*HR)
def staff_edit(request, pk):
    staff = get_object_or_404(StaffProfile, pk=pk)
    form = StaffForm(request.POST or None, instance=staff)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("staff_detail", pk=staff.pk)
    return render(request, "hr/staff_form.html", {"form": form, "title": f"Edit {staff.full_name}", "staff": staff})


@require_POST
@role_required(*HR)
def contract_add(request, pk):
    staff = get_object_or_404(StaffProfile, pk=pk)
    form = ContractForm(request.POST)
    if form.is_valid():
        c = form.save(commit=False)
        c.staff = staff
        clash = (
            staff.contracts.filter(start_date__lte=c.end_date or datetime.date.max)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=c.start_date))
            .first()
        )
        if clash:
            messages.error(
                request, f"This overlaps the contract starting {clash.start_date}. End that one first, the day before."
            )
        else:
            c.save()
            messages.success(request, "Contract added.")
    else:
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    return redirect("staff_detail", pk=staff.pk)


@require_POST
@role_required(*HR)
def contract_end(request, pk):
    c = get_object_or_404(Contract, pk=pk)
    try:
        day = datetime.date.fromisoformat(request.POST.get("end_date", ""))
    except ValueError:
        day = None
    if day is None or day < c.start_date:
        messages.error(request, "Give an end date that is not before the start date.")
    else:
        c.end_date = day
        c.save()
        messages.success(request, "Contract ended.")
    return redirect("staff_detail", pk=c.staff_id)


@require_POST
@role_required(*HR)
def licence_add(request, pk):
    staff = get_object_or_404(StaffProfile, pk=pk)
    form = LicenceForm(request.POST)
    if form.is_valid():
        lic = form.save(commit=False)
        lic.staff = staff
        lic.save()
        messages.success(request, "Licence recorded.")
    else:
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    return redirect("staff_detail", pk=staff.pk)


@role_required(*HR_VIEW)
def licence_alerts(request):
    return render(
        request, "hr/licence_alerts.html", {"rows": services.licence_problems(), "today": timezone.localdate()}
    )


@role_required(*HR_VIEW)
def department_list(request):
    can_edit = request.user.has_role(*HR)
    form = DepartmentForm(request.POST or None)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "Department added.")
            return redirect("department_list")
    return render(
        request,
        "hr/departments.html",
        {"rows": Department.objects.all(), "form": form, "can_edit": can_edit},
    )


# ---------- leave ----------


@role_required(*EVERYONE)
def my_leave(request):
    staff = own_profile(request.user)
    if staff is None:
        return render(request, "hr/not_linked.html", {"what": "leave"})
    form = LeaveRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.request_leave(
                staff,
                form.cleaned_data["leave_type"],
                form.cleaned_data["start_date"],
                form.cleaned_data["end_date"],
                form.cleaned_data["reason"],
                request.user,
            )
            messages.success(request, "Leave requested. You will see the decision here.")
            return redirect("my_leave")
        except ValidationError as exc:
            _errors(request, exc)
    return render(
        request,
        "hr/my_leave.html",
        {
            "staff": staff,
            "form": form,
            "balances": services.balances_for(staff),
            "requests": staff.leave_requests.all()[:30],
        },
    )


@require_POST
@role_required(*EVERYONE)
def leave_cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    mine = leave.staff.user_id == request.user.pk
    if not (mine or request.user.has_role(*HR_VIEW)):
        raise PermissionDenied
    try:
        services.cancel_leave(leave, request.user)
        messages.success(request, "Leave cancelled.")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("my_leave" if mine else "leave_approvals")


@role_required(*HR_VIEW)
def leave_approvals(request):
    waiting = LeaveRequest.objects.filter(status=LeaveRequest.Status.REQUESTED).select_related(
        "staff", "staff__department", "leave_type"
    )
    coming = LeaveRequest.objects.filter(
        status=LeaveRequest.Status.APPROVED, end_date__gte=timezone.localdate()
    ).select_related("staff", "staff__department", "leave_type")[:50]
    return render(request, "hr/leave_approvals.html", {"waiting": waiting, "coming": coming})


@require_POST
@role_required(*HR_VIEW)
def leave_decide(request, pk, action):
    if action not in ("approve", "reject"):
        raise Http404
    leave = get_object_or_404(LeaveRequest, pk=pk)
    try:
        leave, released = services.decide_leave(leave, action == "approve", request.user, request.POST.get("note", ""))
        text = "approved" if action == "approve" else "rejected"
        extra = f" {released} planned shift(s) were taken off the roster." if released else ""
        messages.success(request, f"Leave {text}.{extra}")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("leave_approvals")


@role_required(*HR_VIEW)
def leave_balances(request):
    can_edit = request.user.has_role(*HR)
    form = AdjustLeaveForm(request.POST or None)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            try:
                services.adjust_leave(
                    form.cleaned_data["staff"],
                    form.cleaned_data["leave_type"],
                    form.cleaned_data["days"],
                    form.cleaned_data["note"],
                    request.user,
                )
                messages.success(request, "Balance adjusted.")
                return redirect("leave_balances")
            except ValidationError as exc:
                _errors(request, exc)
    types = list(LeaveType.objects.filter(is_active=True, uses_balance=True))
    rows = []
    for s in StaffProfile.objects.filter(Q(date_left__isnull=True) | Q(date_left__gte=timezone.localdate())):
        rows.append({"staff": s, "cells": [services.balance(s, t) for t in types]})
    return render(
        request,
        "hr/leave_balances.html",
        {"types": types, "rows": rows, "form": form, "can_edit": can_edit},
    )


@require_POST
@role_required(*HR)
def leave_accrue(request):
    try:
        month = datetime.date.fromisoformat(request.POST.get("month", "") + "-01")
    except ValueError:
        messages.error(request, "Choose a month.")
        return redirect("leave_balances")
    if month > timezone.localdate():
        messages.error(request, "Leave cannot be added for a month that has not started.")
        return redirect("leave_balances")
    added = services.accrue_leave(month, request.user)
    messages.success(request, f"{added} entries added for {month:%B %Y}." if added else "That month was already done.")
    return redirect("leave_balances")


@role_required(*HR)
def leave_types(request):
    form = LeaveTypeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Leave type added.")
        return redirect("leave_types")
    return render(request, "hr/leave_types.html", {"rows": LeaveType.objects.all(), "form": form})


@role_required(*HR)
def holidays(request):
    form = HolidayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Holiday added.")
        return redirect("holidays")
    return render(
        request,
        "hr/holidays.html",
        {"rows": PublicHoliday.objects.filter(date__year__gte=timezone.localdate().year - 1), "form": form},
    )
