"""Settings for the test suite."""

import os

os.environ.setdefault("DJANGO_DEBUG", "true")

from .settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Most tests sign in as an administrator with force_login; the two-factor tests switch this back on.
TWO_FACTOR_REQUIRED = False
