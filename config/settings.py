"""Django settings, read from environment variables (see .env.example)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-dev-key-do-not-use-in-production"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
if env("VERCEL"):
    ALLOWED_HOSTS += [".vercel.app"]
    if env("VERCEL_PROJECT_PRODUCTION_URL"):
        ALLOWED_HOSTS.append(env("VERCEL_PROJECT_PRODUCTION_URL"))
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
if env("VERCEL_PROJECT_PRODUCTION_URL"):
    CSRF_TRUSTED_ORIGINS.append("https://" + env("VERCEL_PROJECT_PRODUCTION_URL"))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    # Must come before every app that reacts to saves: its change capture then queues each record before
    # anything created from it (an invoice made when an appointment is saved), so parents always travel first.
    "apps.sync",
    "apps.accounts",
    "apps.patients",
    "apps.appointments",
    "apps.clinical",
    "apps.orders",
    "apps.pharmacy_rx",
    "apps.wards",
    "apps.imaging",
    "apps.maternity",
    "apps.dental",
    "apps.billing",
    "apps.hr",
    "apps.stock",
    "apps.ops",
    "apps.golive",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.CurrentUserMiddleware",
    "apps.accounts.twofactor.TwoFactorMiddleware",
    "apps.accounts.passwords.PasswordChangeMiddleware",
    "apps.ops.middleware.CloudReadOnlyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.hospital",
                "apps.imaging.context_processors.critical_alerts",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if env("DATABASE_URL"):
    # Hosted database (Supabase, Neon, Vercel Postgres...). Used on Vercel, where there is no local PostgreSQL.
    from urllib.parse import unquote, urlparse

    _u = urlparse(env("DATABASE_URL"))
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _u.path.lstrip("/") or "postgres",
            "USER": unquote(_u.username or ""),
            "PASSWORD": unquote(_u.password or ""),
            "HOST": _u.hostname,
            "PORT": str(_u.port or 5432),
            "CONN_MAX_AGE": 0,  # serverless: do not hold connections between requests
            "OPTIONS": {"sslmode": "require"},
            "DISABLE_SERVER_SIDE_CURSORS": True,  # needed behind a transaction pooler
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "hms"),
            # POSTGRES_USER is the account the running website uses. In production it has only the rights it needs
            # (see apply_db_grants). Migrations and apply_db_grants run as the owner when HMS_USE_OWNER_DB is true.
            "USER": env("POSTGRES_OWNER_USER", "hms") if env("HMS_USE_OWNER_DB") == "true" else env("POSTGRES_USER", "hms"),
            "PASSWORD": env("POSTGRES_OWNER_PASSWORD", "hms")
            if env("HMS_USE_OWNER_DB") == "true"
            else env("POSTGRES_PASSWORD", "hms"),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Uploaded files (scan printouts). Served only through views that check the role; back this folder up.
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
# Link to the web image viewer, e.g. https://viewer.hospital.local/viewer?accession={accession}. Empty hides the link.
IMAGING_VIEWER_URL = env("IMAGING_VIEWER_URL", "")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Hospital identity shown in the UI and on printouts.
HOSPITAL_NAME = env("HOSPITAL_NAME", "[HOSPITAL NAME]")
HOSPITAL_NUMBER_PREFIX = env("HOSPITAL_NUMBER_PREFIX", "HN")

# Sessions: staff are signed out after 30 minutes without activity.
SESSION_COOKIE_AGE = 60 * 30
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Shared secret for the M-Pesa confirmation endpoint. Leave empty to keep the endpoint switched off.
MPESA_CALLBACK_SECRET = os.environ.get("MPESA_CALLBACK_SECRET", "")

# Hybrid deployment. "local" is the hospital server, "cloud" is the read-only copy, "training" is a separate
# practice copy with made-up patients: it shows a banner and never syncs or backs up.
HMS_MODE = env("HMS_MODE", "local")
SYNC_CLOUD_URL = env("SYNC_CLOUD_URL", "")  # local server: where to send changes, e.g. https://hms.example.org
SYNC_TOKEN = env("SYNC_TOKEN", "")  # shared secret between the two servers
SYNC_CAPTURE = HMS_MODE != "training"  # write an Outbox row for every change

# Operations: backups, health checks and alerts.
BACKUP_DIR = env("BACKUP_DIR", str(BASE_DIR / "backups"))  # point this at the second disk
BACKUP_KEY_FILE = env("BACKUP_KEY_FILE", "")  # file holding the backup passphrase; keep a copy somewhere safe
BACKUP_COPY_COMMAND = env("BACKUP_COPY_COMMAND", "")  # e.g. "rclone copy {file} remote:hms-backups"
HEALTH_DISK_MIN_FREE_PERCENT = int(env("HEALTH_DISK_MIN_FREE_PERCENT", "15"))
HEALTH_BACKUP_MAX_HOURS = int(env("HEALTH_BACKUP_MAX_HOURS", "30"))
HEALTH_SYNC_MAX_ROWS = int(env("HEALTH_SYNC_MAX_ROWS", "500"))
HEALTH_SYNC_MAX_AGE_MINUTES = int(env("HEALTH_SYNC_MAX_AGE_MINUTES", "60"))
ALERT_EMAILS = [e.strip() for e in env("ALERT_EMAILS", "").split(",") if e.strip()]
ALERT_SMS_WEBHOOK = env("ALERT_SMS_WEBHOOK", "")  # a URL that accepts {"message", "to"} and sends the SMS
ALERT_SMS_NUMBERS = [n.strip() for n in env("ALERT_SMS_NUMBERS", "").split(",") if n.strip()]
ALERT_REPEAT_HOURS = int(env("ALERT_REPEAT_HOURS", "6"))
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "25"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", "false").lower() == "true"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "hms@localhost")

# Security: sign-in lockout and two-factor for administrators.
LOCKOUT_ATTEMPTS = int(env("LOCKOUT_ATTEMPTS", "5"))  # wrong passwords for one username ...
LOCKOUT_ATTEMPTS_PER_IP = int(env("LOCKOUT_ATTEMPTS_PER_IP", "20"))  # ... or from one address ...
LOCKOUT_MINUTES = int(env("LOCKOUT_MINUTES", "15"))  # ... within this time lock sign-in for the same time
TWO_FACTOR_REQUIRED = env("TWO_FACTOR_REQUIRED", "true").lower() == "true"

# Deploy-check warnings we accept, and why (also in docs/security.md):
#  W005: HSTS for subdomains. The server uses one local hospital name, so there are no subdomains to cover.
#  W021: HSTS preload. Preloading needs a public domain registered with browsers; a local name cannot be listed.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
