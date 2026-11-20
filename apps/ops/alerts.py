import datetime
import json
import logging
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .checks import run_all
from .models import CheckState

log = logging.getLogger(__name__)


def send_alert(subject, body):
    """Email IT and, when configured, post to an SMS gateway webhook. Failures are logged, never raised."""
    sent = False
    if settings.ALERT_EMAILS:
        try:
            send_mail(f"[{settings.HOSPITAL_NAME}] {subject}", body, None, settings.ALERT_EMAILS, fail_silently=False)
            sent = True
        except Exception:  # noqa: BLE001
            log.exception("Alert email failed")
    if settings.ALERT_SMS_WEBHOOK:
        try:
            request = urllib.request.Request(
                settings.ALERT_SMS_WEBHOOK,
                data=json.dumps({"message": f"{subject}: {body}"[:300], "to": settings.ALERT_SMS_NUMBERS}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=15)  # noqa: S310
            sent = True
        except Exception:  # noqa: BLE001
            log.exception("Alert SMS failed")
    return sent


def process(results=None, notify=send_alert, now=None):
    """Compare results with what we knew. Alert on new failures, reminders, and recoveries. Returns failing names."""
    now = now or timezone.now()
    results = results if results is not None else run_all()
    repeat = datetime.timedelta(hours=settings.ALERT_REPEAT_HOURS)
    failing = []
    for name, (ok, message) in results.items():
        state, _ = CheckState.objects.get_or_create(name=name)
        if ok:
            if state.failing_since:
                notify(f"RECOVERED: {name}", message)
                state.failing_since = state.last_alerted_at = None
        else:
            failing.append(name)
            if not state.failing_since:
                state.failing_since = now
            if state.last_alerted_at is None or now - state.last_alerted_at >= repeat:
                notify(f"FAILED: {name}", message)
                state.last_alerted_at = now
        state.message, state.updated_at = message, now
        state.save()
    return failing
