"""Single entry point for the Vercel build step.

Runs, in order, exactly what the old chained buildCommand ran:

    python scripts/check_db_url.py
    python manage.py makemigrations --check --dry-run
    python manage.py migrate --noinput
    python manage.py ensure_admin
    python manage.py seed_demo
    python manage.py sync_service_items
    python manage.py seed_sample_data
    python manage.py backfill_charges
    python manage.py collectstatic --noinput

Fails fast on the first error, same as the original `&&`-chained command.
Exists so vercel.json's buildCommand can stay under the 256-character schema limit:

    "buildCommand": "python manage.py build_pipeline"
"""

import subprocess
import sys

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

# Management commands to run, in order, between `migrate` and `collectstatic`.
STEPS = [
    "ensure_admin",
    "seed_demo",
    "sync_service_items",
    "seed_sample_data",
    "backfill_charges",
]


class Command(BaseCommand):
    help = "Runs the full Vercel build pipeline (DB check, migrations, seed/sync commands, collectstatic)."

    def handle(self, *args, **options):
        self.run_check_db_url()

        self.stdout.write(self.style.MIGRATE_HEADING("Checking for missing migrations..."))
        call_command("makemigrations", check=True, dry_run=True)

        self.stdout.write(self.style.MIGRATE_HEADING("Applying migrations..."))
        call_command("migrate", interactive=False)

        for step in STEPS:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Running {step}..."))
            call_command(step)

        self.stdout.write(self.style.MIGRATE_HEADING("Collecting static files..."))
        call_command("collectstatic", interactive=False)

        self.stdout.write(self.style.SUCCESS("Build pipeline complete."))

    def run_check_db_url(self):
        """scripts/check_db_url.py is a standalone script, not a management command, so it
        is run as a subprocess (same as the original buildCommand). Its own sys.exit(...)
        message on a failed connection is printed as before; we just turn a non-zero exit
        into a CommandError so this command's failure is reported the same way as the rest
        of the pipeline's steps."""
        script = settings.BASE_DIR / "scripts" / "check_db_url.py"
        result = subprocess.run([sys.executable, str(script)])
        if result.returncode != 0:
            raise CommandError("check_db_url.py failed; see output above.")
