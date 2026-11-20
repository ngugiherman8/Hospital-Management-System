from . import audit
from .models import AuditLog


class CurrentUserMiddleware:
    """Makes the signed-in user and client IP available to the audit trail, and logs refused requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None
        request.audit_ip = ip
        tokens = audit.set_context(user=request.user, ip=ip)
        try:
            response = self.get_response(request)
            if response.status_code == 403:
                AuditLog.objects.create(
                    user=audit.current_user(),
                    ip_address=ip,
                    action="denied",
                    model_label="access",
                    object_id=request.path[:64],
                    object_repr=f"{request.method} {request.path}"[:200],
                )
            return response
        finally:
            audit.reset_context(tokens)
