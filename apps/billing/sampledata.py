"""Sample prices for TESTING ONLY. The hospital sets its real prices in the admin site."""

from decimal import Decimal

from apps.patients.models import PayerType

from .models import Price, ServiceItem
from .services import sync_service_items

BASE = {"REG": 200, "CONSULT": 500, "BED-GENERAL": 1500, "BED-MATERNITY": 2000, "BED-PAEDIATRIC": 1200,
        "BED-PRIVATE": 4000}  # fmt: skip
BY_CATEGORY = {"lab": 400, "imaging": 2500, "procedure": 500, "drug": 50, "dental": 800, "other": 100}


def seed_sample_prices():
    """Give every billable item a sample price (SHA at 80 percent). Does nothing if prices already exist."""
    sync_service_items()
    if Price.objects.exists():
        return 0
    n = 0
    for s in ServiceItem.objects.all():
        base = Decimal(BASE.get(s.code, BY_CATEGORY.get(s.category, 100)))
        for ptype in PayerType.values:
            amount = (base * Decimal("0.8")).quantize(Decimal("1")) if ptype == PayerType.SHA else base
            Price.objects.create(service=s, payer_type=ptype, amount=amount)
            n += 1
    return n
