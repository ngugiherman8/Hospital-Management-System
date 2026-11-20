import csv
import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.golive import setup_data


class Command(BaseCommand):
    help = (
        "Load users, prices, drugs, tests, wards and counted opening stock from CSV files in a folder "
        "(see apps/golive/setup_data.py for the columns). All or nothing; safe to run again."
    )

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--counted-on", help="Date the opening stock was counted (YYYY-MM-DD). Default today.")
        parser.add_argument("--user", help="Username to record as having received the opening stock.")
        parser.add_argument(
            "--credentials-out",
            default="new_accounts.csv",
            help="Where to write the temporary passwords of new accounts. Hand them out, then delete the file.",
        )

    def handle(self, *args, **opts):
        counted_on = datetime.date.fromisoformat(opts["counted_on"]) if opts["counted_on"] else None
        user = User.objects.filter(username=opts["user"]).first() if opts["user"] else None
        try:
            credentials = setup_data.load_folder(
                opts["folder"], counted_on=counted_on, user=user, log=lambda m: self.stdout.write(m)
            )
        except setup_data.SetupError as exc:
            raise CommandError(f"Nothing was loaded. {exc}") from None
        if credentials:
            with open(opts["credentials_out"], "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["username", "role", "temporary_password"])
                writer.writerows(credentials)
            self.stdout.write(
                self.style.WARNING(
                    f"{len(credentials)} new accounts. Temporary passwords are in {opts['credentials_out']}; "
                    "each person must choose their own at first sign-in. Delete the file after handing them out."
                )
            )
