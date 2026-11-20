"""Cloud side: apply each change once, in order, keyed by model, record id and version."""

from django.db import DatabaseError, transaction

from .capture import model_for
from .models import AppliedChange

NEVER_APPLY = {"sync.Outbox", "sync.AppliedChange"}


class BadChange(Exception):
    pass


def _build(model, data):
    fields = {f.attname: f for f in model._meta.concrete_fields}
    values = {}
    for attname, value in data.items():
        field = fields.get(attname)
        if field is None:
            continue  # a column this version does not have
        values[attname] = value if field.is_relation or value is None else field.to_python(value)
    return model(**values)


def apply_change(label, object_id, version, action, data):
    """Apply one change. Returns "applied", "stale" (a newer version is already here) or "duplicate"."""
    if label in NEVER_APPLY:
        raise BadChange(f"{label} is not synchronised")
    try:
        model = model_for(label)
    except (LookupError, ValueError):
        raise BadChange(f"Unknown model {label}") from None
    with transaction.atomic():
        if AppliedChange.objects.filter(model_label=label, object_id=object_id, version=version).exists():
            return "duplicate"
        newest = (
            AppliedChange.objects.filter(model_label=label, object_id=object_id, applied=True)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        if newest is not None and newest > version:
            AppliedChange.objects.create(model_label=label, object_id=object_id, version=version, applied=False)
            return "stale"
        if action == "delete":
            model._base_manager.filter(pk=object_id).delete()
        else:
            if not isinstance(data, dict):
                raise BadChange("A save needs data")
            _build(model, data).save_base(raw=True)  # no signals, no audit, no outbox: this is a copy
        AppliedChange.objects.create(model_label=label, object_id=object_id, version=version)
    return "applied"


def apply_batch(changes):
    """Apply a batch in order, all or nothing. Returns (acked sequence numbers, error or None).

    One transaction for the whole batch: the database checks foreign keys when it commits, so a row may arrive
    before the row it points to, as long as both are in the same batch (the sender keeps transactions whole).
    """
    seq = None
    try:
        with transaction.atomic():
            for change in changes:
                seq = change.get("seq")
                apply_change(
                    change["model"],
                    str(change["object_id"]),
                    int(change["version"]),
                    change["action"],
                    change.get("data"),
                )
    except (BadChange, KeyError, ValueError, TypeError, DatabaseError) as exc:
        return [], f"change {seq}: {type(exc).__name__}: {str(exc)[:200]}"
    return [c["seq"] for c in changes], None
