"""Charge the completed nights of every patient still in a bed. Run once a day, just after midnight."""

from django.core.management.base import BaseCommand

from apps.billing.services import charge_bed_nights
from apps.wards.models import Admission


class Command(BaseCommand):
    help = "Add bed-day charges for everyone currently admitted (safe to run twice)."

    def handle(self, *args, **options):
        made = sum(charge_bed_nights(a) for a in Admission.objects.filter(discharged_at__isnull=True))
        self.stdout.write(f"{made} bed-day charge(s) added.")
