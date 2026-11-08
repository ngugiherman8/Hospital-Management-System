from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.appointments.models import Stage, TransitionError
from apps.clinical.workspace import CARE, LAB, VisitPostView
from apps.core.permissions import RoleRequiredMixin

from .forms import CorrectionForm, OrderForm, ResultForm
from .models import Order, Result


def _return_from_lab(request, order):
    """When the last open test is resulted or cancelled, the patient goes back to the doctor."""
    entry = order.visit.queue_entry
    if entry and entry.stage == Stage.AT_LAB:
        try:
            entry.apply("lab_complete", request.user)
        except TransitionError:
            pass  # other tests are still open


def can_result(user, order):
    """Lab and radiology are resulted by lab staff; procedures by the clinician who did them."""
    return user.has_role(*CARE) if order.test.kind == "procedure" else user.has_role(*LAB)


class OrderAddView(VisitPostView):
    def act(self, request):
        form = OrderForm(request.POST)
        if not form.is_valid():
            return self.invalid(request, order_form=form)
        d = form.cleaned_data
        order = Order.objects.create(
            visit=self.visit,
            test=d["test"],
            ordered_by=request.user,
            urgent=d["urgent"],
            clinical_notes=d["clinical_notes"],
        )
        messages.success(request, f"{order.test.name} ordered ({order.number}).")
        return redirect(self.back)


class OrderCancelView(VisitPostView):
    def act(self, request, order_pk):
        order = get_object_or_404(Order, pk=order_pk, visit=self.visit)
        if order.status == Order.Status.ORDERED:
            order.status = Order.Status.CANCELLED
            order.save()
            _return_from_lab(request, order)
        else:
            messages.error(request, "Only tests the lab has not started can be cancelled.")
        return redirect(self.back)


class LabWorklistView(RoleRequiredMixin, View):
    """Lab technicians work it; doctors and nurses can look."""

    allowed_roles = ("lab", "doctor", "nurse")

    def get(self, request):
        open_orders = (
            Order.objects.filter(status__in=["ordered", "in_progress"])
            .select_related("test", "visit__patient", "ordered_by")
            .order_by("-urgent", "ordered_at")
        )
        since = timezone.now() - timezone.timedelta(hours=24)
        done = list(
            Order.objects.filter(status="resulted", results__resulted_at__gte=since)
            .distinct()
            .select_related("test", "visit__patient")
            .order_by("-id")[:20]
        )
        for o in done:
            o.result = o.current_result
        return render(
            request,
            "orders/lab.html",
            {
                "open_orders": open_orders,
                "done": done,
                "result_form": ResultForm(),
                "correction_form": CorrectionForm(),
                "is_lab": request.user.has_role(*LAB),
                "is_care": request.user.has_role(*CARE),
            },
        )


class LabCollectView(RoleRequiredMixin, View):
    allowed_roles = LAB
    http_method_names = ["post"]

    def post(self, request, order_pk):
        order = get_object_or_404(Order, pk=order_pk)
        if order.status == Order.Status.ORDERED:
            order.status = Order.Status.IN_PROGRESS
            order.sample_taken_at = timezone.now()
            order.save()
        return redirect("orders:lab")


class LabResultView(RoleRequiredMixin, View):
    allowed_roles = ("lab", "doctor", "nurse")
    http_method_names = ["post"]

    def post(self, request, order_pk):
        order = get_object_or_404(Order.objects.select_related("test", "visit"), pk=order_pk)
        if not can_result(request.user, order):
            raise PermissionDenied
        if not order.is_open:
            messages.error(request, "That order is not waiting for a result.")
            return redirect("orders:lab")
        form = ResultForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter the result and mark it normal, high, low or abnormal.")
            return redirect("orders:lab")
        d = form.cleaned_data
        with transaction.atomic():
            Result.objects.create(
                order=order,
                value=d["value"],
                unit=d["unit"],
                flag=d["flag"],
                comment=d["comment"],
                resulted_by=request.user,
            )
            order.status = Order.Status.RESULTED
            order.sample_taken_at = order.sample_taken_at or timezone.now()
            order.save()
            _return_from_lab(request, order)
        messages.success(request, f"Result saved for {order.test.name}.")
        return redirect("orders:lab")


class LabCorrectView(RoleRequiredMixin, View):
    """A correction adds a new result that replaces the old one on screen. The old one stays on record."""

    allowed_roles = ("lab", "doctor", "nurse")
    http_method_names = ["post"]

    def post(self, request, order_pk):
        order = get_object_or_404(Order.objects.select_related("test"), pk=order_pk)
        if not can_result(request.user, order):
            raise PermissionDenied
        current = order.current_result
        if order.status != Order.Status.RESULTED or current is None:
            messages.error(request, "Only a test that already has a result can be corrected.")
            return redirect("orders:lab")
        form = CorrectionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "A correction needs the new result and the reason for the change.")
            return redirect("orders:lab")
        d = form.cleaned_data
        Result.objects.create(
            order=order,
            value=d["value"],
            unit=d["unit"],
            flag=d["flag"],
            comment=d["comment"],
            resulted_by=request.user,
            corrects=current,
        )
        messages.success(request, "Correction saved. The earlier result is kept in the record.")
        return redirect("orders:lab")
