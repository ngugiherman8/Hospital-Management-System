"""Local side: send unsent Outbox rows to the cloud in order, backing off when the cloud cannot be reached."""

import datetime
import json
import urllib.error
import urllib.request

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import Outbox

BATCH = 200
MAX_WAIT = datetime.timedelta(hours=1)


def backoff(attempts):
    """5 s, 10 s, 20 s ... up to an hour."""
    return min(datetime.timedelta(seconds=5 * 2 ** min(attempts, 12)), MAX_WAIT)


def http_post(payload):
    """Send a batch to the cloud. Returns the parsed reply. Raises OSError-like errors on failure."""
    request = urllib.request.Request(
        settings.SYNC_CLOUD_URL.rstrip("/") + "/sync/receive/",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.SYNC_TOKEN}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (URL comes from settings)
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except ValueError:
            raise OSError(f"Cloud answered {exc.code}") from exc


def run_once(post=http_post, now=None, batch=BATCH):
    """Send one batch. Returns {"sent": n, "error": str or None}. Rows stay in order: a failure stops the batch."""
    now = now or timezone.now()
    rows = list(Outbox.objects.filter(sent_at__isnull=True)[:batch])
    if not rows:
        return {"sent": 0, "error": None}
    last = rows[-1]
    if last.txid:  # never cut a database transaction in half: a child row may need its parent from the same one
        rows += list(Outbox.objects.filter(sent_at__isnull=True, txid=last.txid, id__gt=last.id))
    head = rows[0]
    if head.next_attempt_at and head.next_attempt_at > now:
        return {"sent": 0, "error": "waiting to retry"}
    payload = {
        "changes": [
            {"seq": r.id, "model": r.model_label, "object_id": r.object_id, "version": r.version,
             "action": r.action, "data": r.data}
            for r in rows
        ]
    }  # fmt: skip
    try:
        reply = post(payload)
        acked, error = set(reply.get("acked", [])), reply.get("error")
    except (OSError, ValueError) as exc:  # no internet, timeout, garbled reply
        acked, error = set(), f"{type(exc).__name__}: {exc}"[:300]
    done = [r.id for r in rows if r.id in acked]
    if done:
        Outbox.objects.filter(id__in=done).update(sent_at=now, attempts=0, next_attempt_at=None, last_error="")
    if error or len(done) < len(rows):
        failed = next((r for r in rows if r.id not in acked), None)
        if failed:
            attempts = failed.attempts + 1
            Outbox.objects.filter(pk=failed.pk).update(
                attempts=attempts,
                next_attempt_at=now + backoff(attempts),
                last_error=(error or "not acknowledged")[:300],
            )
    return {"sent": len(done), "error": error}


def queue_status(now=None):
    now = now or timezone.now()
    unsent = Outbox.objects.filter(sent_at__isnull=True)
    oldest = unsent.order_by("id").values_list("created_at", flat=True).first()
    last = Outbox.objects.filter(sent_at__isnull=False).order_by("-sent_at").values_list("sent_at", flat=True).first()
    head = unsent.order_by("id").first()
    return {
        "unsent": unsent.count(),
        "oldest_age_seconds": int((now - oldest).total_seconds()) if oldest else 0,
        "last_sent_at": last,
        "last_error": head.last_error if head else "",
        "retrying": unsent.filter(~Q(last_error="")).exists(),
    }
