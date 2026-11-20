"""Fill a practice copy with made-up data so each department can rehearse. Never runs on the real server."""

import datetime

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.patients.models import Patient
from apps.pharmacy_rx.models import Drug
from apps.stock import services as stock_services
from apps.stock.models import GoodsReceipt, Supplier

FIRST = ["Asha", "Baraka", "Chebet", "Dalila", "Eliud", "Faith", "Gathoni", "Hassan", "Imani", "Juma"]


class Command(BaseCommand):
    help = "Practice accounts (train-<role>), made-up patients, catalogue, prices and stock. HMS_MODE=training only."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="practice-2026-go")
        parser.add_argument("--patients", type=int, default=30)
        parser.add_argument("--reset", action="store_true", help="Wipe this practice copy first.")

    def handle(self, *args, **opts):
        if settings.HMS_MODE != "training":
            raise CommandError(
                "This only runs on a training copy (HMS_MODE=training). It will not touch the real data."
            )
        if opts["reset"]:
            try:
                call_command("flush", interactive=False, verbosity=0)
            except CommandError:
                raise CommandError(
                    "Wiping needs the database owner account. Run it as: "
                    "docker compose ... exec -e HMS_USE_OWNER_DB=true web python manage.py seed_training --reset"
                ) from None
        call_command("seed_clinical", verbosity=0)
        call_command("seed_billing", verbosity=0)
        for role in Role:
            user, created = User.objects.get_or_create(
                username=f"train-{role.value}",
                defaults={
                    "role": role.value,
                    "first_name": "Practice",
                    "last_name": role.label,
                    "is_staff": role == Role.ADMIN,
                },
            )
            if created:
                user.set_password(opts["password"])
                user.save()
        for i in range(1, opts["patients"] + 1):
            Patient.objects.get_or_create(
                first_name=FIRST[i % len(FIRST)],
                last_name=f"Practice{i:02d}",
                date_of_birth=datetime.date(1960 + i % 50, 1 + i % 12, 1 + i % 28),
                defaults={
                    "sex": "FM"[i % 2],
                    "phone": f"+2547000{i:05d}",
                    "residence": "Practice village (made up)",
                    "payer": Patient.Payer.CASH if i % 4 else Patient.Payer.SHA,
                },
            )
        supplier, _ = Supplier.objects.get_or_create(name="Practice supplier")
        receipt = GoodsReceipt.objects.create(supplier=supplier, supplier_invoice="PRACTICE")
        far = datetime.date.today() + datetime.timedelta(days=365)
        for drug in Drug.objects.exclude(batches__batch_number="PRACTICE-1"):
            stock_services.receive_stock(receipt, drug, "PRACTICE-1", far, 200, 5, None)
        self.stdout.write(
            self.style.SUCCESS(
                "Training copy ready. Sign in as train-<role> (for example train-reception) with the practice password."
            )
        )
