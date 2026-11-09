"""Login lockout and one-time codes (TOTP, works with Google Authenticator, Authy, Microsoft Authenticator)."""

import io

import pyotp
import segno
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import LoginEvent


def recent_failures(username: str) -> int:
    """Failed attempts for this username since the last good sign-in, inside the lockout window."""
    since = timezone.now() - timezone.timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    last_ok = LoginEvent.objects.filter(username__iexact=username, kind="login").order_by("-at").first()
    if last_ok and last_ok.at > since:
        since = last_ok.at
    return LoginEvent.objects.filter(username__iexact=username, kind="failed", at__gt=since).count()


def is_locked(username: str) -> bool:
    return bool(username) and recent_failures(username) >= settings.LOGIN_LOCKOUT_ATTEMPTS


class LockoutAuthenticationForm(AuthenticationForm):
    """Refuses even a correct password while the account is locked after repeated failures."""

    def clean(self):
        username = self.cleaned_data.get("username") or self.data.get("username", "")
        if is_locked(username):
            raise ValidationError(
                f"Too many failed attempts. Try again in {settings.LOGIN_LOCKOUT_MINUTES} minutes, "
                "or ask an administrator.",
                code="locked",
            )
        return super().clean()


def needs_second_factor(user) -> bool:
    staff_admin = user.is_superuser or getattr(user, "role", "") == "admin"
    return bool(user.two_factor_required or (settings.ADMIN_TWO_FACTOR and staff_admin))


def new_secret() -> str:
    return pyotp.random_base32()


def verify_code(secret: str, code: str) -> bool:
    return bool(secret) and pyotp.TOTP(secret).verify((code or "").replace(" ", ""), valid_window=1)


def provisioning_uri(user, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=settings.TWO_FACTOR_ISSUER)


def qr_svg(uri: str) -> str:
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True)
    return buf.getvalue().decode()
