from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve

EXEMPT = {"login", "logout", "password_change", "otp_setup", "otp_verify", "health", "sync_receive", "mpesa_confirm"}


class PasswordChangeMiddleware:
    """Accounts made by the set-up loader carry a password someone else knows, so it must be replaced first."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.must_change_password:
            try:
                name = resolve(request.path_info).url_name
            except Resolver404:
                name = None
            if name not in EXEMPT and not request.path_info.startswith(settings.STATIC_URL):
                return redirect("password_change")
        return self.get_response(request)


def password_change(request):
    if not request.user.is_authenticated:
        return redirect("login")
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, user)
        messages.success(request, "Your password has been changed.")
        return redirect("home")
    return render(request, "security/password_change.html", {"form": form, "forced": request.user.must_change_password})
