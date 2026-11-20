from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import FailedLogin, lockout_minutes_left


class LockoutAuthenticationForm(AuthenticationForm):
    """Sign-in form that refuses everyone, even with the right password, while the account is locked."""

    def clean(self):
        username = self.cleaned_data.get("username", "")
        ip = getattr(self.request, "audit_ip", None) if self.request else None
        left = lockout_minutes_left(username, ip) if username else 0
        if left:
            raise forms.ValidationError(
                f"Too many wrong passwords. Try again in {left} minute(s), or ask an administrator.", code="locked"
            )
        cleaned = super().clean()
        if self.user_cache is not None:
            FailedLogin.objects.filter(username__iexact=username).delete()
        return cleaned


class CodeForm(forms.Form):
    code = forms.CharField(max_length=7, label="6-digit code from your authenticator app")
