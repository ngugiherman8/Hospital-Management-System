"""Audit trail: who changed what, and when.

Models that inherit from `AuditedModel` are logged automatically on create,
update and delete. Reads of sensitive records are logged with `log_access`.
The current user and IP address come from `CurrentUserMiddleware`.
"""

import contextvars
import datetime
import decimal
import uuid

from django.db.models.signals import post_delete, post_save, pre_save

_current_user = contextvars.ContextVar("audit_user", default=None)
_current_ip = contextvars.ContextVar("audit_ip", default=None)


def set_context(user=None, ip=None):
    return _current_user.set(user), _current_ip.set(ip)


def reset_context(tokens):
    _current_user.reset(tokens[0])
    _current_ip.reset(tokens[1])


def current_user():
    user = _current_user.get()
    return user if getattr(user, "is_authenticated", False) else None


def current_ip():
    return _current_ip.get()


def _jsonable(value):
    if hasattr(value, "name") and hasattr(value, "storage"):  # an uploaded file: log its name only
        return value.name
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    return value


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name == "updated_at" or field.name in getattr(instance, "audit_exclude", ()):
            continue
        data[field.name] = _jsonable(field.value_from_object(instance))
    return data


def _write(action, instance, changes):
    from .models import AuditLog

    AuditLog.objects.create(
        user=current_user(),
        ip_address=current_ip(),
        action=action,
        model_label=instance._meta.label,
        object_id=str(instance.pk),
        object_repr=str(instance)[:200],
        changes=changes,
    )


def log_access(instance, note=""):
    """Record that the current user viewed or printed a record."""
    _write("view", instance, {"note": note} if note else {})


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or not _audited(instance):
        return
    instance._audit_old = None
    if instance.pk:
        old = sender._base_manager.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_old = _snapshot(old)


def _post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not _audited(instance):
        return
    new = _snapshot(instance)
    if created:
        _write("create", instance, {k: {"new": v} for k, v in new.items()})
        return
    old = getattr(instance, "_audit_old", None) or {}
    changes = {k: {"old": old.get(k), "new": v} for k, v in new.items() if old.get(k) != v}
    if changes:
        _write("update", instance, changes)


def _post_delete(sender, instance, **kwargs):
    if _audited(instance):
        _write("delete", instance, _snapshot(instance))


pre_save.connect(_pre_save, dispatch_uid="audit_pre_save")
post_save.connect(_post_save, dispatch_uid="audit_post_save")
post_delete.connect(_post_delete, dispatch_uid="audit_post_delete")


def _audited(instance):
    return isinstance(instance, (AuditedModel, AppendOnlyModel))


# Imported last: models import this module, so avoid a circular import at load.
from .appendonly import AppendOnlyModel  # noqa: E402
from .models import AuditedModel  # noqa: E402
