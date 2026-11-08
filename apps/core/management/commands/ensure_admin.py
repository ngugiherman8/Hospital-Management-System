"""Create or update a full administrator from ADMIN_USERNAME / ADMIN_PASSWORD (safe to run on every deploy)."""

import os

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import User


class Command(BaseCommand):
    help = "Creates a superuser from the ADMIN_USERNAME and ADMIN_PASSWORD environment variables."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME", "").strip()
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not username and not password:
            self.stdout.write("ADMIN_USERNAME / ADMIN_PASSWORD not set; skipping admin creation.")
            return
        if not username or not password:
            raise CommandError("Set both ADMIN_USERNAME and ADMIN_PASSWORD.")
        if len(password) < 10:
            raise CommandError("ADMIN_PASSWORD must be at least 10 characters.")
        user, created = User.objects.get_or_create(username=username)
        user.role = "admin"
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Administrator '{username}' {'created' if created else 'updated'}."))
