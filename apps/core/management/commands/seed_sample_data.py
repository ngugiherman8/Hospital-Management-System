"""Fake patients, appointments and a live queue for testing. Runs only when SEED_SAMPLE_DATA=true (or --force)."""
import datetime
import os

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.appointments.models import Appointment, Clinic, QueueEntry
from apps.core.models import User
from apps.patients.models import InsuranceCover, Patient

# (first, last, dob, sex, phone, area, payer, member, allergies)
PEOPLE = [
    ("Wanjiru", "Kamau", (1992, 3, 14), "F", "0712 000 001", "Kasarani, Nairobi", "sha", "SHA-TEST-0001", "Penicillin"),
    ("Brian", "Otieno", (2019, 5, 2), "M", "0712 000 002", "Kibera, Nairobi", "cash", "", ""),
    ("Amina", "Hassan", (1974, 8, 21), "F", "0712 000 003", "Eastleigh, Nairobi", "private", "INS-TEST-0003", ""),
    ("Joseph", "Mwangi", (1981, 1, 30), "M", "0712 000 004", "Thika", "sha", "SHA-TEST-0004", ""),
    ("Grace", "Achieng", (1997, 11, 9), "F", "0712 000 005", "Rongai", "sha", "SHA-TEST-0005", ""),
    ("Samuel", "Kiptoo", (1988, 6, 17), "M", "0712 000 006", "Ngong", "cash", "", "Sulfa drugs"),
    ("Mercy", "Wambui", (2001, 4, 4), "F", "0712 000 007", "Ruiru", "corporate", "CORP-TEST-0007", ""),
    ("Peter", "Njoroge", (1959, 12, 2), "M", "0712 000 008", "Kiambu", "cash", "", ""),
]
# queue: index into PEOPLE -> (stage, minutes ago)
QUEUE = {0: ("waiting_triage", 2), 1: ("waiting_triage", 9), 2: ("waiting_doctor", 18), 3: ("waiting_doctor", 24),
         4: ("with_doctor", 12), 5: ("at_lab", 30)}


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        if not opts["force"] and os.environ.get("SEED_SAMPLE_DATA", "").lower() not in ("1", "true", "yes"):
            self.stdout.write("SEED_SAMPLE_DATA not set; skipping sample data.")
            return
        if Patient.objects.exists():
            self.stdout.write("Patients already exist; sample data not added.")
            return
        clinic = Clinic.objects.get_or_create(name="General outpatient")[0]
        doctor = User.objects.filter(role="doctor").first()
        now = timezone.now()

        patients = []
        for first, last, dob, sex, phone, area, payer, member, allergies in PEOPLE:
            p = Patient.objects.create(
                first_name=first, last_name=last, date_of_birth=datetime.date(*dob), sex=sex, phone=phone,
                area_of_residence=area, id_type="none", allergies=allergies,
                next_of_kin_name="Test Relative", next_of_kin_relationship="Spouse", next_of_kin_phone="0722 000 000")
            InsuranceCover.objects.create(
                patient=p, payer_type=payer, member_number=member,
                insurer_name={"private": "Test Insurer", "corporate": "Test Company Ltd"}.get(payer, ""))
            patients.append(p)

        for idx, (stage, minutes) in QUEUE.items():
            assigned = doctor if stage != "waiting_triage" else None
            entry, _ = QueueEntry.join(patient=patients[idx], clinic=clinic, doctor=assigned)
            when = now - datetime.timedelta(minutes=minutes)
            entry.stage = stage
            entry.arrived_at = entry.stage_changed_at = when
            entry.room = "2" if stage == "with_doctor" else ""
            entry.save()

        if doctor:
            for i, idx in enumerate((6, 7)):
                Appointment.objects.create(
                    patient=patients[idx], clinic=clinic, doctor=doctor, reason="Review",
                    starts_at=now + datetime.timedelta(hours=3 + i), created_by=doctor)
            Appointment.objects.create(patient=patients[0], clinic=clinic, doctor=doctor, reason="Follow-up",
                                       starts_at=now + datetime.timedelta(days=1, hours=1), created_by=doctor)
        self.stdout.write(self.style.SUCCESS("Added 8 fake patients, a live queue and 3 appointments."))
