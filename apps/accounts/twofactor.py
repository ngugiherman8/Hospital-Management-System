from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import resolve
from django.utils import timezone

from . import totp
from .forms import CodeForm
from .models import TOTPDevice

EXEMPT = {"login", "logout", "otp_setup", "otp_verify", "health", "sync_receive", "mpesa_confirm"}


def needs_second_factor(user):
    return settings.TWO_FACTOR_REQUIRED and (user.is_superuser or user.is_staff or user.role in ("admin", "manager"))


class TwoFactorMiddleware:
    """Administrators must pass a second factor after the password before they can use anything."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and needs_second_factor(user) and not request.session.get("otp_ok"):
            from django.urls import Resolver404

            try:
                name = resolve(request.path_info).url_name
            except Resolver404:
                name = None
            if name not in EXEMPT and not request.path_info.startswith(settings.STATIC_URL):
                device = TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
                return redirect("otp_verify" if device else "otp_setup")
        return self.get_response(request)


def otp_setup(request):
    if not request.user.is_authenticated:
        return redirect("login")
    device = TOTPDevice.objects.filter(user=request.user).first()
    if device and device.confirmed_at:
        return redirect("otp_verify")
    if device is None:
        device = TOTPDevice.objects.create(user=request.user, secret=totp.new_secret())
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        step = totp.verify(device.secret, form.cleaned_data["code"])
        if step:
            device.confirmed_at, device.last_step = timezone.now(), step
            device.save()
            request.session["otp_ok"] = True
            messages.success(request, "Two-factor sign-in is switched on for your account.")
            return redirect("home")
        form.add_error("code", "That code is not right. Check the time on your phone and try again.")
    uri = totp.provisioning_uri(device.secret, request.user.get_username(), settings.HOSPITAL_NAME)
    return render(request, "security/otp_setup.html", {"form": form, "secret": device.secret, "uri": uri})


def otp_verify(request):
    if not request.user.is_authenticated:
        return redirect("login")
    device = TOTPDevice.objects.filter(user=request.user, confirmed_at__isnull=False).first()
    if device is None:
        return redirect("otp_setup")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        step = totp.verify(device.secret, form.cleaned_data["code"], after_step=device.last_step)
        if step:
            device.last_step = step
            device.save()
            request.session["otp_ok"] = True
            return redirect(request.GET.get("next") or "home")
        form.add_error("code", "That code is not right or was already used.")
    return render(request, "security/otp_verify.html", {"form": form})
