"""Make sure every catalogue entry is billable and report charges that still need a price.

Safe to run again. It never invents a price: anything unpriced stays as an open ChargeAlert for the cashier.
"""

from django.core.management.base import BaseCommand

from apps.billing.models import ChargeAlert
from apps.billing.services import sync_service_items


class Command(BaseCommand):
    def handle(self, *args, **opts):
        made = sync_service_items()
        open_alerts = ChargeAlert.objects.filter(resolved_at__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(f"{made} billable items created. {open_alerts} charge alerts still open."))
