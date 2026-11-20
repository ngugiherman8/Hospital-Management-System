"""Create one sign-in per role so each screen can be tried out. Development only."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User


class Command(BaseCommand):
    help = "Create a demo user for every role (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="demo-password-123")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs when DJANGO_DEBUG is on.")
        for role in Role:
            user, created = User.objects.get_or_create(
                username=role.value,
                defaults={"role": role.value, "first_name": role.label, "is_staff": role == Role.ADMIN},
            )
            if created:
                user.set_password(options["password"])
                user.save()
            self.stdout.write(f"{'created' if created else 'exists '} {user.username} ({role.label})")
