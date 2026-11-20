import hmac
import json

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.permissions import role_required

from .models import Outbox
from .receiver import apply_batch
from .sender import queue_status


@csrf_exempt
@require_POST
def receive(request):
    """Cloud only: apply a batch of changes sent by the hospital server. Needs the shared token."""
    if settings.HMS_MODE != "cloud" or not settings.SYNC_TOKEN:
        raise Http404
    expected = f"Bearer {settings.SYNC_TOKEN}"
    if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
        raise PermissionDenied
    try:
        changes = json.loads(request.body)["changes"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"acked": [], "error": "bad request"}, status=400)
    acked, error = apply_batch(changes)
    return JsonResponse({"acked": acked, "error": error}, status=422 if error else 200)


@role_required()  # administrators only
def status(request):
    return render(
        request,
        "ops/sync_status.html",
        {
            "q": queue_status(),
            "mode": settings.HMS_MODE,
            "configured": bool(settings.SYNC_CLOUD_URL and settings.SYNC_TOKEN),
            "stuck": Outbox.objects.filter(sent_at__isnull=True).exclude(last_error="")[:10],
        },
    )
