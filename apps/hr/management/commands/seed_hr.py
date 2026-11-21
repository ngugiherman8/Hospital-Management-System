from django.core.management.base import BaseCommand

from apps.hr.models import Department, LeaveType

DEPARTMENTS = [
    "Outpatient", "Ward", "Maternity", "Theatre", "Laboratory", "Pharmacy", "Imaging", "Dental",
    "Administration and finance", "Support services",
]  # fmt: skip

# Starting figures only. The hospital's own HR policy and the Employment Act decide the real ones: check them.
LEAVE = [
    ("Annual leave", 21, True, True),
    ("Sick leave", 0, False, True),
    ("Maternity leave", 0, False, True),
    ("Paternity leave", 0, False, True),
    ("Compassionate leave", 0, False, True),
    ("Study leave", 0, False, True),
    ("Unpaid leave", 0, False, False),
]


class Command(BaseCommand):
    help = "Create the usual departments and leave types if they are missing. Safe to run again."

    def handle(self, *args, **options):
        made = 0
        for name in DEPARTMENTS:
            made += Department.objects.get_or_create(name=name)[1]
        for name, days, uses_balance, paid in LEAVE:
            made += LeaveType.objects.get_or_create(
                name=name, defaults={"annual_days": days, "uses_balance": uses_balance, "is_paid": paid}
            )[1]
        self.stdout.write(self.style.SUCCESS(f"{made} new records. Check the leave days against your HR policy."))
