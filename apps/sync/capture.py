"""Change capture: every save or delete of an audited record adds an Outbox row in the same transaction."""

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db.models import Max
from django.db.models.signals import post_delete, post_save

from apps.core.appendonly import AppendOnlyModel
from apps.core.audit import _jsonable
from apps.core.models import AuditedModel, AuditLog

TRACKED_BASES = (AuditedModel, AppendOnlyModel, AuditLog)


def tracked(instance):
    return isinstance(instance, TRACKED_BASES)


def snapshot(instance):
    """Every column of the row, keyed by column attribute, in JSON-safe form."""
    audit_exclude = getattr(instance, "audit_exclude", ())
    return {
        f.attname: _jsonable(f.value_from_object(instance))
        for f in instance._meta.concrete_fields
        if f.name not in audit_exclude
    }


def _lock_and_txid(label, object_id):
    """One writer at a time per record until the transaction ends. Returns the transaction's id."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s)), txid_current()", [f"{label}:{object_id}"])
        return cursor.fetchone()[1]


def _next_version(label, object_id):
    from .models import Outbox

    last = Outbox.objects.filter(model_label=label, object_id=object_id).aggregate(m=Max("version"))["m"]
    return (last or 0) + 1


def record(instance, action="save"):
    from .models import Outbox

    label, object_id = instance._meta.label, str(instance.pk)
    txid = _lock_and_txid(label, object_id)
    Outbox.objects.create(
        model_label=label,
        object_id=object_id,
        txid=txid,
        version=_next_version(label, object_id),
        action=action,
        data=snapshot(instance),
    )


def _post_save(sender, instance, raw=False, **kwargs):
    if raw or not getattr(settings, "SYNC_CAPTURE", True) or not tracked(instance):
        return
    record(instance)


def _post_delete(sender, instance, **kwargs):
    if getattr(settings, "SYNC_CAPTURE", True) and tracked(instance):
        record(instance, "delete")


def connect():
    post_save.connect(_post_save, dispatch_uid="sync_post_save")
    post_delete.connect(_post_delete, dispatch_uid="sync_post_delete")


def model_for(label):
    return apps.get_model(label)
