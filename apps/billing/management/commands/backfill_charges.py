"""Bring every visit's draft charges up to date. Safe to run again: each clinical event is charged once."""

from django.core.management.base import BaseCommand

from apps.billing.services import sync_charges
from apps.clinical.models import Visit


class Command(BaseCommand):
    def handle(self, *args, **opts):
        n = 0
        for visit in Visit.objects.select_related("patient"):
            sync_charges(visit)
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Checked charges for {n} visits."))
