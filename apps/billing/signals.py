"""Clinical events become draft charges. A billing fault must never block patient care, so each sync runs in its
own savepoint and any error is logged instead of raised."""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


def _safe_sync(visit):
    from .services import sync_charges

    try:
        with transaction.atomic():
            sync_charges(visit)
    except Exception:  # noqa: BLE001
        log.exception("Could not update the charges for visit %s", getattr(visit, "pk", "?"))


@receiver(post_save, sender="clinical.Visit")
def _visit_saved(sender, instance, created, **kwargs):
    if created or instance.finished_at or instance.status == "closed":
        _safe_sync(instance)


@receiver(post_save, sender="orders.Order")
def _order_saved(sender, instance, **kwargs):
    _safe_sync(instance.visit)


@receiver(post_save, sender="wards.Admission")
def _admission_saved(sender, instance, **kwargs):
    _safe_sync(instance.visit)


@receiver(post_save, sender="pharmacy_rx.Prescription")
def _prescription_saved(sender, instance, **kwargs):
    if instance.status == "dispensed":
        _safe_sync(instance.visit)
