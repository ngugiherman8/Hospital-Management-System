"""Create a cloud-only sign-in. Ids start at 1,000,000 so they never clash with staff synced from the hospital."""

import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max

from apps.accounts.models import Role, User

FIRST_ID = 1_000_000


class Command(BaseCommand):
    help = "Create a cloud-only account (the cloud copy does not receive passwords from the hospital)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--role", default=Role.MANAGER, choices=[r.value for r in Role])

    def handle(self, *args, **options):
        if User.objects.filter(username=options["username"]).exists():
            raise CommandError("That username is taken.")
        password = getpass.getpass("Password: ")
        if not password:
            raise CommandError("A password is needed.")
        pk = max((User.objects.aggregate(m=Max("pk"))["m"] or 0) + 1, FIRST_ID)
        role = options["role"]
        user = User(pk=pk, username=options["username"], role=role, is_staff=role == Role.ADMIN)
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Created {user.username} ({role}) with id {pk}."))
