from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.urls import Resolver404, resolve

SAFE = {"GET", "HEAD", "OPTIONS"}
# The cloud copy only accepts sign-in and sign-out, the sync endpoint and the two-factor screens.
CLOUD_WRITABLE = {"login", "logout", "sync_receive", "otp_verify", "otp_setup"}


class CloudReadOnlyMiddleware:
    """On the cloud copy nothing can be changed through the website. Data arrives only through sync."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.HMS_MODE == "cloud" and request.method not in SAFE:
            try:
                name = resolve(request.path_info).url_name
            except Resolver404:
                name = None  # the normal 404 page will answer
            if name is not None and name not in CLOUD_WRITABLE:
                raise PermissionDenied("This is the read-only cloud copy.")
        return self.get_response(request)
