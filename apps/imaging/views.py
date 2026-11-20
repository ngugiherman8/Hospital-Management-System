from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import CLINICIAN_ROLES, Role
from apps.appointments.models import Appointment
from apps.clinical.views import EDITABLE, _clinic_ok
from apps.core.audit import log_access
from apps.core.permissions import role_required
from apps.orders.models import Order, OrderItem

from .forms import ImagingOrderForm, ReportForm, StudyFileForm
from .models import (
    ImagingModality,
    ImagingReport,
    ImagingStudy,
    StudyFile,
    acknowledge_critical,
    save_report,
)

VIEWERS = (Role.RADIOGRAPHER, Role.RADIOLOGIST, *CLINICIAN_ROLES)


@role_required(*CLINICIAN_ROLES)
@require_POST
def imaging_order(request, pk):
    visit = get_object_or_404(Appointment.objects.select_related("patient"), pk=pk)
    if visit.status not in EDITABLE or not _clinic_ok(request.user, visit):
        raise PermissionDenied
    form = ImagingOrderForm(request.POST, patient=visit.patient)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("visit_detail", pk=visit.pk)
    with transaction.atomic():
        order = Order.objects.create(appointment=visit, kind="imaging", ordered_by=request.user)
        for imaging_item in form.cleaned_data["studies"]:
            item = OrderItem.objects.create(order=order, test=imaging_item.test)
            ImagingStudy.objects.create(
                order_item=item,
                patient=visit.patient,
                item=imaging_item,
                clinical_reason=form.cleaned_data["clinical_reason"],
                **form.answers(),
            )
    messages.success(request, f"Imaging ordered: {order.number}.")
    return redirect("visit_detail", pk=visit.pk)


@role_required(*VIEWERS)
def worklist(request):
    modality = request.GET.get("modality", "")
    studies = ImagingStudy.objects.exclude(
        status__in=(ImagingStudy.Status.CANCELLED, ImagingStudy.Status.FINAL)
    ).select_related("patient", "item__test", "item__modality")
    if modality.isdigit():
        studies = studies.filter(item__modality_id=int(modality))
    return render(
        request,
        "imaging/worklist.html",
        {"studies": studies, "modalities": ImagingModality.objects.filter(is_active=True), "modality": modality},
    )


@role_required(*VIEWERS)
def study_detail(request, pk):
    study = get_object_or_404(
        ImagingStudy.objects.select_related("patient", "item__test", "item__modality", "order_item__order"), pk=pk
    )
    log_access(study)
    user = request.user
    reports = study.reports.select_related("author").prefetch_related("acks__doctor")
    ctx = {
        "study": study,
        "reports": reports,
        "files": study.files.all(),
        "is_radiographer": user.has_role(Role.RADIOGRAPHER),
        "is_radiologist": user.has_role(Role.RADIOLOGIST),
        "can_acknowledge": user.has_role(*CLINICIAN_ROLES) and study.unacknowledged_critical is not None,
        "report_form": ReportForm(),
        "file_form": StudyFileForm(),
        "viewer_url": _viewer_url(study),
        "visit": study.order_item.order.appointment,
    }
    return render(request, "imaging/study.html", ctx)


def _viewer_url(study):
    from django.conf import settings

    template = getattr(settings, "IMAGING_VIEWER_URL", "")
    return template.replace("{accession}", study.accession_number) if template else ""


@role_required(Role.RADIOGRAPHER)
@require_POST
def study_action(request, pk):
    study = get_object_or_404(ImagingStudy, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "confirm_safety":
            study.confirm_safety(request.user)
            messages.success(request, "Safety questions confirmed.")
        elif action in ("arrived", "images_done"):
            study.move_to(action, request.user)
            messages.success(request, f"Study marked: {study.get_status_display()}.")
        else:
            raise PermissionDenied
    except PermissionError as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("study_detail", pk=study.pk)


@role_required(Role.RADIOLOGIST)
@require_POST
def report_save(request, pk):
    study = get_object_or_404(ImagingStudy, pk=pk)
    form = ReportForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            report = save_report(
                study,
                request.user,
                data["findings"],
                data["impression"],
                critical=data["critical"],
                final=data["sign_final"],
                reason=data["reason"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request, f"Report saved as {report.get_status_display().lower()}, version {report.version}."
            )
    else:
        messages.error(request, "Findings and impression are both needed.")
    return redirect("study_detail", pk=study.pk)


@role_required(Role.RADIOGRAPHER, Role.RADIOLOGIST)
@require_POST
def file_upload(request, pk):
    study = get_object_or_404(ImagingStudy, pk=pk)
    form = StudyFileForm(request.POST, request.FILES)
    if form.is_valid():
        StudyFile.objects.create(
            study=study, file=form.cleaned_data["file"], note=form.cleaned_data["note"], added_by=request.user
        )
        messages.success(request, "File attached.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("study_detail", pk=study.pk)


@role_required(*VIEWERS)
def file_download(request, pk):
    attachment = get_object_or_404(StudyFile, pk=pk)
    log_access(attachment)
    try:
        return FileResponse(attachment.file.open("rb"), filename=attachment.file.name.rsplit("/", 1)[-1])
    except FileNotFoundError:
        raise Http404 from None


@role_required(*CLINICIAN_ROLES)
@require_POST
def critical_acknowledge(request, pk):
    report = get_object_or_404(ImagingReport, pk=pk, critical=True)
    try:
        acknowledge_critical(report, request.user)
    except PermissionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Critical finding acknowledged.")
    target = request.POST.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect("study_detail", pk=report.study_id)
