"""Create starter clinics, departments and one demo user per role (development only)."""
import os

from django.core.management.base import BaseCommand

from apps.appointments.models import Clinic
from apps.core.models import Department, Role, User

DEPARTMENTS = ["Reception", "Outpatient", "Maternal and child health", "Laboratory", "Pharmacy", "Billing"]
CLINICS = ["General outpatient", "Maternal and child health"]
USERS = [("admin1", Role.ADMIN), ("reception1", Role.RECEPTIONIST), ("doctor1", Role.DOCTOR),
         ("nurse1", Role.NURSE), ("lab1", Role.LAB), ("pharmacy1", Role.PHARMACIST), ("cashier1", Role.CASHIER)]


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", "ChangeMe-12345"))

    def handle(self, *args, **opts):
        for name in DEPARTMENTS:
            Department.objects.get_or_create(code=name.lower().replace(" ", "-"), defaults={"name": name})
        for name in CLINICS:
            Clinic.objects.get_or_create(name=name)
        for username, role in USERS:
            user, created = User.objects.get_or_create(
                username=username, defaults={"role": role, "first_name": username.rstrip("1").title(),
                                             "is_staff": role == Role.ADMIN})
            if created:
                user.set_password(opts["password"])
                user.save()
        self.stdout.write(self.style.SUCCESS("Seeded departments, clinics and demo users."))
