from .base import *  # noqa

DEBUG = False
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
CSRF_TRUSTED_ORIGINS = [o for o in env("DJANGO_CSRF_ORIGINS", "").split(",") if o]  # noqa: F405
if SECRET_KEY.startswith("dev-only"):  # noqa: F405
    raise RuntimeError("Set DJANGO_SECRET_KEY in production")

# HSTS for all subdomains and the browser preload list are long-lived commitments. Turn them on only when the
# hospital's domain is final and every subdomain is served over HTTPS.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
