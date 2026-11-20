from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from .models import FailedLogin


@receiver(user_login_failed)
def remember_failure(sender, credentials, request=None, **kwargs):
    username = (credentials or {}).get("username", "")
    if username:
        ip = getattr(request, "audit_ip", None) if request else None
        FailedLogin.objects.create(username=username[:150], ip_address=ip)


@receiver(user_logged_in)
def reset_second_factor(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session"):
        request.session["otp_ok"] = False  # a new sign-in always has to pass the second factor again
