from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from . import twofactor
from .middleware import client_ip
from .models import LoginEvent, User

PENDING_KEY = "pending_2fa"
PENDING_SECONDS = 5 * 60


class HospitalLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = twofactor.LockoutAuthenticationForm

    def form_valid(self, form):
        user = form.get_user()
        if not twofactor.needs_second_factor(user):
            return super().form_valid(form)
        nxt = self.get_redirect_url() or settings.LOGIN_REDIRECT_URL
        self.request.session[PENDING_KEY] = {"user_id": user.pk, "next": nxt, "at": timezone.now().timestamp()}
        return redirect("two_factor")


class TwoFactorView(View):
    """Second step of sign-in. First time round it also enrols the authenticator app."""

    def _pending_user(self, request):
        data = request.session.get(PENDING_KEY)
        if not data or timezone.now().timestamp() - data["at"] > PENDING_SECONDS:
            request.session.pop(PENDING_KEY, None)
            return None, None
        return User.objects.filter(pk=data["user_id"], is_active=True).first(), data

    def _page(self, request, user, error=""):
        enrolling = not user.totp_confirmed_at
        ctx = {"enrolling": enrolling, "error": error, "username": user.username}
        if enrolling:
            if not user.totp_secret:
                user.totp_secret = twofactor.new_secret()
                user.save(update_fields=["totp_secret"])
            ctx["secret"] = user.totp_secret
            ctx["qr"] = twofactor.qr_svg(twofactor.provisioning_uri(user, user.totp_secret))
        return render(request, "registration/two_factor.html", ctx)

    def get(self, request):
        user, _ = self._pending_user(request)
        if not user:
            messages.error(request, "Sign in again.")
            return redirect("login")
        return self._page(request, user)

    def post(self, request):
        user, data = self._pending_user(request)
        if not user:
            messages.error(request, "Sign in again.")
            return redirect("login")
        if twofactor.is_locked(user.username):
            request.session.pop(PENDING_KEY, None)
            messages.error(request, "Too many failed attempts. Try again later or ask an administrator.")
            return redirect("login")
        if not twofactor.verify_code(user.totp_secret, request.POST.get("code", "")):
            LoginEvent.objects.create(username=user.username, kind="failed", ip_address=client_ip(request))
            return self._page(request, user, error="That code is not right. Check the code and try again.")
        if not user.totp_confirmed_at:
            user.totp_confirmed_at = timezone.now()
            user.save(update_fields=["totp_confirmed_at"])
        request.session.pop(PENDING_KEY, None)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        nxt = data["next"]
        if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
            nxt = settings.LOGIN_REDIRECT_URL
        return redirect(nxt)
