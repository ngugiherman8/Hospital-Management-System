from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import CLINICIAN_ROLES, Role
from apps.appointments.models import Appointment, Status
from apps.clinical.views import EDITABLE, _clinic_ok
from apps.core.audit import log_access
from apps.core.permissions import role_required

from .forms import OrderForm, ResultForm
from .models import Order, OrderItem, record_result


@role_required(*CLINICIAN_ROLES)
@require_POST
def order_create(request, pk):
    visit = get_object_or_404(Appointment, pk=pk)
    if visit.status not in EDITABLE or not _clinic_ok(request.user, visit):
        raise PermissionDenied
    form = OrderForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            order = Order.objects.create(appointment=visit, ordered_by=request.user, note=form.cleaned_data["note"])
            for test in form.cleaned_data["tests"]:
                OrderItem.objects.create(order=order, test=test)
        messages.success(request, f"Ordered {order.items.count()} test(s): {order.number}.")
    else:
        messages.error(request, "Choose at least one test.")
    return redirect("visit_detail", pk=visit.pk)


@role_required(*CLINICIAN_ROLES)
@require_POST
def item_cancel(request, pk):
    item = get_object_or_404(OrderItem.objects.select_related("order__appointment"), pk=pk)
    visit = item.order.appointment
    if visit.status not in EDITABLE or not _clinic_ok(request.user, visit):
        raise PermissionDenied
    if item.status == OrderItem.Status.RESULT_IN:
        messages.error(request, "A test with a result cannot be cancelled.")
    else:
        item.status = OrderItem.Status.CANCELLED
        item.save()
        study = getattr(item, "study", None)
        if study is not None:
            study.cancel()
        messages.success(request, f"{item.test.name} cancelled.")
    return redirect("visit_detail", pk=visit.pk)


@role_required(Role.LAB, Role.DOCTOR, Role.NURSE)
def worklist(request):
    """Tests waiting for a sample or a result, oldest first."""
    items = (
        OrderItem.objects.filter(status__in=OrderItem.PENDING, order__kind="lab")
        .select_related("order__appointment__patient", "test")
        .order_by("order__created_at", "id")
    )
    return render(request, "orders/worklist.html", {"items": items, "can_work": request.user.has_role(Role.LAB)})


@role_required(Role.LAB)
@require_POST
def sample_taken(request, pk):
    item = get_object_or_404(OrderItem, pk=pk)
    if item.status == OrderItem.Status.ORDERED:
        item.status = OrderItem.Status.SAMPLE_TAKEN
        item.save()
    return redirect("lab_worklist")


@role_required(Role.LAB)
def result_entry(request, pk):
    item = get_object_or_404(OrderItem.objects.select_related("order__appointment__patient", "test"), pk=pk)
    visit = item.order.appointment
    if item.status == OrderItem.Status.CANCELLED:
        raise PermissionDenied
    previous = item.current_result
    initial = {"unit": item.test.unit}
    form = ResultForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        record_result(item, data["value"], request.user, unit=data["unit"], flag=data["flag"], comment=data["comment"])
        messages.success(request, f"Result saved for {item.test.name}.")
        visit.refresh_from_db()
        if visit.status == Status.AWAITING_RESULTS and Status.WITH_DOCTOR in visit.allowed_next(request.user):
            try:
                visit.move_to(Status.WITH_DOCTOR, request.user)
                messages.success(request, f"All results are in: {visit.patient.full_name} is back with the doctor.")
            except Exception:  # noqa: BLE001 - other tests are still open; the visit stays where it is
                pass
        return redirect("lab_worklist")
    if previous:
        log_access(previous)
    return render(
        request, "orders/result_form.html", {"item": item, "visit": visit, "form": form, "previous": previous}
    )
