"""Create a billable item for every test and drug in the catalogues, so each can be given a price."""

from django.core.management.base import BaseCommand

from apps.billing.services import sync_service_items


class Command(BaseCommand):
    def handle(self, *args, **opts):
        self.stdout.write(self.style.SUCCESS(f"{sync_service_items()} billable items."))
