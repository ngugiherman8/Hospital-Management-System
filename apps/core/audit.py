"""Automatic audit trail.

Any model that subclasses AuditedModel gets create/update/delete entries written
for free. Views, prints and emergency overrides are logged with log_event().
"""

import threading

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

_state = threading.local()


def set_context(user=None, ip=None):
    _state.user = user
    _state.ip = ip


def get_user():
    user = getattr(_state, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def get_ip():
    return getattr(_state, "ip", None)


class AuditedModel(models.Model):
    """Abstract base: changes are written to the audit log automatically."""

    audit_exclude = ()

    class Meta:
        abstract = True


def _snapshot(instance):
    data = {}
    for f in instance._meta.concrete_fields:
        if f.name in instance.audit_exclude or getattr(f, "auto_now", False):
            continue  # auto timestamps change on every save and are not real edits
        data[f.name] = str(f.value_from_object(instance))
    return data


def log_event(action, obj=None, *, reason="", changes=None, needs_review=False, user=None, ip=None):
    from .models import AuditLog

    return AuditLog.objects.create(
        user=user or get_user(),
        action=action,
        content_type=ContentType.objects.get_for_model(obj) if obj is not None else None,
        object_id=str(obj.pk) if obj is not None else "",
        object_repr=str(obj)[:200] if obj is not None else "",
        changes=changes or {},
        reason=reason,
        ip_address=ip or get_ip(),
        needs_review=needs_review,
    )


def log_access(obj, reason=""):
    """Record that a user opened a clinical record."""
    return log_event("view", obj, reason=reason)


@receiver(pre_save)
def _remember_old(sender, instance, **kwargs):
    if not isinstance(instance, AuditedModel):
        return
    instance._audit_old = None
    if instance.pk and not kwargs.get("raw"):
        old = sender._default_manager.filter(pk=instance.pk).first()
        if old:
            instance._audit_old = _snapshot(old)


@receiver(post_save)
def _audit_save(sender, instance, created, **kwargs):
    if not isinstance(instance, AuditedModel) or kwargs.get("raw"):
        return
    new = _snapshot(instance)
    if created:
        log_event("create", instance, changes={k: [None, v] for k, v in new.items()})
        return
    old = getattr(instance, "_audit_old", None) or {}
    diff = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
    if diff:
        log_event("update", instance, changes=diff)


@receiver(post_delete)
def _audit_delete(sender, instance, **kwargs):
    if isinstance(instance, AuditedModel):
        log_event("delete", instance, changes=_snapshot(instance))
