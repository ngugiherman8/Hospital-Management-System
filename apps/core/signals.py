from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .middleware import client_ip
from .models import LoginEvent


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    LoginEvent.objects.create(
        user=user, username=user.username, kind="login", ip_address=client_ip(request) if request else None
    )


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user:
        LoginEvent.objects.create(
            user=user, username=user.username, kind="logout", ip_address=client_ip(request) if request else None
        )


@receiver(user_login_failed)
def _failed(sender, credentials, request=None, **kwargs):
    LoginEvent.objects.create(
        username=credentials.get("username", "")[:150],
        kind="failed",
        ip_address=client_ip(request) if request else None,
    )
