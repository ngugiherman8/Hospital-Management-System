from . import audit


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class CurrentUserMiddleware:
    """Makes the acting user and IP available to the audit signals."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        audit.set_context(user=getattr(request, "user", None), ip=client_ip(request))
        try:
            return self.get_response(request)
        finally:
            audit.set_context(None, None)
