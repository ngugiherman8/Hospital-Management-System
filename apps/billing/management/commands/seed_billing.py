"""Starter price list so the billing screens work on a new server. Every price is a placeholder."""

from django.core.management.base import BaseCommand

from apps.billing.models import ItemPrice, ServiceItem
from apps.dental.models import DentalProcedure
from apps.orders.models import TestCatalogue
from apps.patients.models import Patient
from apps.pharmacy_rx.models import Drug

C = ServiceItem.Category
FIXED = [
    ("REG", "Registration", C.REGISTRATION, 100),
    ("CONS-GENERAL", "General consultation", C.CONSULTATION, 500),
    ("CONS-DENTAL", "Dental consultation", C.CONSULTATION, 600),
    ("CONS-MCH", "Mother and child clinic visit", C.CONSULTATION, 300),
    ("CONS-EYE", "Eye clinic consultation", C.CONSULTATION, 600),
    ("BED-GENERAL", "Bed day, general ward", C.BED, 1500),
    ("BED-MATERNITY", "Bed day, maternity ward", C.BED, 2000),
    ("BED-PAEDIATRIC", "Bed day, paediatric ward", C.BED, 1500),
    ("BED-PRIVATE", "Bed day, private ward", C.BED, 5000),
    ("ANC-CONTACT", "Antenatal contact", C.ANTENATAL, 300),
    ("PNC-CONTACT", "Postnatal contact", C.ANTENATAL, 300),
]


def _price(service, amount):
    # One placeholder price, used for every payer until the hospital sets payer-specific prices.
    ItemPrice.objects.get_or_create(service=service, payer_kind=Patient.Payer.CASH, defaults={"amount": amount})


class Command(BaseCommand):
    help = "Create placeholder service items and self-pay prices (safe to run again)."

    def handle(self, *args, **options):
        for code, name, category, amount in FIXED:
            item, _ = ServiceItem.objects.get_or_create(code=code, defaults={"name": name, "category": category})
            _price(item, amount)
        for test in TestCatalogue.objects.all():
            category = C.IMAGING if test.kind == "imaging" else C.LAB
            item, _ = ServiceItem.objects.get_or_create(
                test=test, defaults={"code": f"T-{test.code}", "name": test.name, "category": category}
            )
            _price(item, test.price or (2500 if test.kind == "imaging" else 500))
        for drug in Drug.objects.all():
            item, _ = ServiceItem.objects.get_or_create(
                drug=drug, defaults={"code": f"D-{drug.pk}", "name": str(drug), "category": C.DRUG}
            )
            _price(item, drug.price or 20)
        for proc in DentalProcedure.objects.all():
            item, _ = ServiceItem.objects.get_or_create(
                dental_procedure=proc, defaults={"code": f"DP-{proc.code}", "name": proc.name, "category": C.DENTAL}
            )
            _price(item, proc.default_price or 1500)
        self.stdout.write(self.style.SUCCESS("Placeholder price list in place. Replace with the hospital's prices."))
