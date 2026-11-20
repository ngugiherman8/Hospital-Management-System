from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditedModel, Sequence
from apps.orders.models import OrderItem, TestCatalogue
from apps.patients.models import Patient


class ImagingModality(AuditedModel):
    """A kind of imaging and its room. Added by an administrator, so a new machine needs no new code."""

    name = models.CharField(max_length=80, unique=True)
    dicom_code = models.CharField("DICOM code", max_length=4, help_text="DX, US, CT, MR, MG, RF, ...")
    room = models.CharField(max_length=80, blank=True)
    can_send_dicom = models.BooleanField(default=False)
    scanner_address = models.CharField(max_length=120, blank=True, help_text="Only if the machine is connected.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "imaging modalities"

    def __str__(self):
        return self.name


class ImagingItem(AuditedModel):
    """The imaging details of a catalogue entry (study name, body part, preparation, safety flags)."""

    modality = models.ForeignKey(ImagingModality, on_delete=models.PROTECT, related_name="items")
    test = models.OneToOneField(
        TestCatalogue, on_delete=models.PROTECT, related_name="imaging", limit_choices_to={"kind": "imaging"}
    )
    body_part = models.CharField(max_length=80, blank=True)
    preparation = models.TextField(blank=True)
    uses_radiation = models.BooleanField(default=False)
    uses_contrast = models.BooleanField(default=False)

    class Meta:
        ordering = ["modality__name", "test__name"]

    def __str__(self):
        return f"{self.test.name} ({self.modality.name})"


class ImagingStudy(AuditedModel):
    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        ARRIVED = "arrived", "Arrived"
        IMAGES_DONE = "images_done", "Images done"
        PRELIMINARY = "preliminary", "Preliminary report"
        FINAL = "final", "Final report"
        CANCELLED = "cancelled", "Cancelled"

    order_item = models.OneToOneField(OrderItem, on_delete=models.PROTECT, related_name="study")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="imaging_studies")
    item = models.ForeignKey(ImagingItem, on_delete=models.PROTECT, related_name="studies")
    accession_number = models.CharField(max_length=30, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ORDERED, db_index=True)
    clinical_reason = models.CharField(max_length=300)
    may_be_pregnant = models.BooleanField(null=True, blank=True, help_text="Asked for radiation studies.")
    contrast_allergy = models.BooleanField(null=True, blank=True)
    kidney_problem = models.BooleanField(null=True, blank=True)
    safety_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    safety_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    arrived_at = models.DateTimeField(null=True, blank=True, editable=False)
    done_at = models.DateTimeField(null=True, blank=True, editable=False)
    radiographer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "imaging studies"

    def __str__(self):
        return f"{self.accession_number} {self.item.test.name}"

    def save(self, *args, **kwargs):
        if not self.accession_number:
            year = timezone.localdate().year
            self.accession_number = f"IMG-{year}-{Sequence.next(f'accession-{year}'):06d}"
        super().save(*args, **kwargs)

    @property
    def needs_safety_check(self):
        return self.item.uses_contrast or (self.item.uses_radiation and self.may_be_pregnant)

    @property
    def safety_confirmed(self):
        return self.safety_confirmed_at is not None

    @property
    def current_report(self):
        return self.reports.first()

    @property
    def ordering_user(self):
        return self.order_item.order.ordered_by

    @property
    def unacknowledged_critical(self):
        report = self.reports.filter(critical=True).first()
        if report and not report.acks.exists():
            return report
        return None

    def confirm_safety(self, user):
        self.safety_confirmed_by = user
        self.safety_confirmed_at = timezone.now()
        self.save()

    def move_to(self, new_status, user):
        """Radiographer moves: ordered -> arrived -> images done. Reports move the rest."""
        allowed = {
            self.Status.ORDERED: [self.Status.ARRIVED],
            self.Status.ARRIVED: [self.Status.IMAGES_DONE],
        }
        if new_status not in allowed.get(self.status, []):
            raise PermissionError(f"A study that is '{self.get_status_display()}' cannot move to {new_status}.")
        if new_status == self.Status.IMAGES_DONE:
            if self.item.uses_contrast and not self.safety_confirmed:
                raise ValidationError("Confirm the contrast safety questions before starting this study.")
            if self.item.uses_radiation and self.may_be_pregnant and not self.safety_confirmed:
                raise ValidationError("The patient may be pregnant. Confirm the pregnancy check before starting.")
            self.done_at = timezone.now()
            self.radiographer = user
            item = self.order_item
            item.status = OrderItem.Status.IN_PROGRESS
            item.save()
        if new_status == self.Status.ARRIVED:
            self.arrived_at = timezone.now()
        self.status = new_status
        self.save()

    def cancel(self):
        if self.status in (self.Status.ORDERED, self.Status.ARRIVED):
            self.status = self.Status.CANCELLED
            self.save()


class ImagingReport(AuditedModel):
    """A saved report version. Never edited: a change is a new version, and a final report is locked."""

    class Status(models.TextChoices):
        PRELIMINARY = "preliminary", "Preliminary"
        FINAL = "final", "Final"

    study = models.ForeignKey(ImagingStudy, on_delete=models.PROTECT, related_name="reports")
    version = models.PositiveIntegerField(editable=False)
    status = models.CharField(max_length=12, choices=Status.choices)
    findings = models.TextField()
    impression = models.TextField()
    critical = models.BooleanField(default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    signed_at = models.DateTimeField(null=True, blank=True, editable=False)
    addendum_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="addenda", editable=False
    )
    addendum_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["study", "version"], name="unique_report_version")]

    def __str__(self):
        return f"{self.study.accession_number} report v{self.version}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Saved reports cannot be changed; save a new version or an addendum.")
        if not self.version:
            last = ImagingReport.objects.filter(study=self.study).order_by("-version").first()
            self.version = (last.version if last else 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Saved reports cannot be deleted.")


class CriticalAck(AuditedModel):
    report = models.ForeignKey(ImagingReport, on_delete=models.PROTECT, related_name="acks")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    acknowledged_at = models.DateTimeField(default=timezone.now, editable=False)

    def __str__(self):
        return f"{self.report} read by {self.doctor}"


class StudyFile(AuditedModel):
    """A JPEG or PDF attached to a study, for machines that cannot send DICOM."""

    study = models.ForeignKey(ImagingStudy, on_delete=models.PROTECT, related_name="files")
    file = models.FileField(upload_to="imaging/%Y/%m/")
    note = models.CharField(max_length=200, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.study.accession_number} file {self.pk}"


def save_report(study, user, findings, impression, critical=False, final=False, reason=""):
    """Save a preliminary report, sign a final one, or add an addendum to a final report."""
    from apps.appointments.models import Status as VisitStatus

    with transaction.atomic():
        study = ImagingStudy.objects.select_for_update().get(pk=study.pk)
        latest = study.current_report
        if study.status in (ImagingStudy.Status.ORDERED, ImagingStudy.Status.ARRIVED, ImagingStudy.Status.CANCELLED):
            raise ValidationError("Images must be done before a report can be written.")
        addendum_of = None
        if latest is not None and latest.status == ImagingReport.Status.FINAL:
            if not reason.strip():
                raise ValidationError("A final report is locked. Give a reason to add an addendum.")
            addendum_of = latest if latest.addendum_of is None else latest.addendum_of
            final = True
        report = ImagingReport.objects.create(
            study=study,
            status=ImagingReport.Status.FINAL if final else ImagingReport.Status.PRELIMINARY,
            findings=findings,
            impression=impression,
            critical=critical or bool(latest and latest.critical and addendum_of),
            author=user,
            signed_at=timezone.now() if final else None,
            addendum_of=addendum_of,
            addendum_reason=reason.strip(),
        )
        if addendum_of is None:
            study.status = ImagingStudy.Status.FINAL if final else ImagingStudy.Status.PRELIMINARY
            study.save()
        item = study.order_item
        if item.status != OrderItem.Status.RESULT_IN:
            item.status = OrderItem.Status.RESULT_IN
            item.save()
        visit = item.order.appointment
        visit.refresh_from_db()
        if visit.status == VisitStatus.AWAITING_RESULTS and VisitStatus.WITH_DOCTOR in visit.allowed_next(user):
            try:
                visit.move_to(VisitStatus.WITH_DOCTOR, user)
            except Exception:  # noqa: BLE001 - other tests are still open
                pass
        return report


def acknowledge_critical(report, user):
    """The ordering clinician (or an administrator) records that they have read a critical finding."""
    orderer = report.study.ordering_user
    is_admin = user.is_superuser or user.role == "admin"
    if not is_admin and (orderer is None or orderer.pk != user.pk):
        raise PermissionError("Only the ordering clinician can acknowledge this finding.")
    return CriticalAck.objects.create(report=report, doctor=user)
