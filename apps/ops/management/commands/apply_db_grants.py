"""Create the limited website database account and set what it may do. Run after every migrate, as the owner."""

import os

from django.core.management.base import BaseCommand, CommandError

from apps.ops import dbroles


class Command(BaseCommand):
    help = "Create or update the website's limited database account (POSTGRES_USER / POSTGRES_PASSWORD)."

    def add_arguments(self, parser):
        parser.add_argument("--user", default=os.environ.get("POSTGRES_USER", ""))
        parser.add_argument("--password", default=os.environ.get("POSTGRES_PASSWORD", ""))

    def handle(self, *args, **o):
        if not o["user"]:
            raise CommandError("Give --user or set POSTGRES_USER.")
        try:
            owner = dbroles.apply(o["user"], o["password"] or None)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if owner == o["user"]:
            raise CommandError("The website account must be different from the owner account running this command.")
        self.stdout.write(f"Website account {o['user']} limited. Owner account: {owner}.")
