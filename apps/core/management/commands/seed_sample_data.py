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
QUEUE = {
    0: ("waiting_triage", 2),
    1: ("waiting_triage", 9),
    2: ("waiting_doctor", 18),
    3: ("waiting_doctor", 24),
    4: ("with_doctor", 12),
    5: ("at_lab", 30),
}


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        if not opts["force"] and os.environ.get("SEED_SAMPLE_DATA", "").lower() not in ("1", "true", "yes"):
            self.stdout.write("SEED_SAMPLE_DATA not set; skipping sample data.")
            return
        from apps.billing.sampledata import seed_sample_prices

        seed_sample_prices()  # prices first, so the charges created below are priced
        if Patient.objects.exists():
            from apps.clinical.models import Visit

            sample = list(Patient.objects.filter(phone__startswith="0712 000").order_by("phone"))
            if len(sample) >= 8 and not Visit.objects.exists():
                self.add_clinical(sample)
                self.stdout.write(self.style.SUCCESS("Added clinical sample data to the existing sample patients."))
            else:
                self.stdout.write("Patients already exist; sample data not added.")
            return
        clinic = Clinic.objects.get_or_create(name="General outpatient")[0]
        doctor = User.objects.filter(role="doctor").first()
        now = timezone.now()

        patients = []
        for first, last, dob, sex, phone, area, payer, member, allergies in PEOPLE:
            p = Patient.objects.create(
                first_name=first,
                last_name=last,
                date_of_birth=datetime.date(*dob),
                sex=sex,
                phone=phone,
                area_of_residence=area,
                id_type="none",
                allergies=allergies,
                next_of_kin_name="Test Relative",
                next_of_kin_relationship="Spouse",
                next_of_kin_phone="0722 000 000",
            )
            InsuranceCover.objects.create(
                patient=p,
                payer_type=payer,
                member_number=member,
                insurer_name={"private": "Test Insurer", "corporate": "Test Company Ltd"}.get(payer, ""),
            )
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
                    patient=patients[idx],
                    clinic=clinic,
                    doctor=doctor,
                    reason="Review",
                    starts_at=now + datetime.timedelta(hours=3 + i),
                    created_by=doctor,
                )
            Appointment.objects.create(
                patient=patients[0],
                clinic=clinic,
                doctor=doctor,
                reason="Follow-up",
                starts_at=now + datetime.timedelta(days=1, hours=1),
                created_by=doctor,
            )
        self.add_clinical(patients)
        self.stdout.write(
            self.style.SUCCESS("Added 8 fake patients, a live queue, 3 appointments and clinical records.")
        )

    def add_clinical(self, patients):
        """Triage records, a patient at the lab, an admitted patient, and past visits."""
        from apps.clinical.models import ClinicalNote, ICD10Code, Visit, VisitDiagnosis, VitalSigns
        from apps.orders.models import Order, Result, TestCatalogue
        from apps.pharmacy_rx.models import Drug, Prescription
        from apps.wards.models import Admission, Bed, BedMove

        now = timezone.now()
        today = timezone.localdate()
        doctor = User.objects.filter(role="doctor").first()
        nurse = User.objects.filter(role="nurse").first()
        lab = User.objects.filter(role="lab").first()

        def dx(visit, code, title, kind="provisional"):
            icd, _ = ICD10Code.objects.get_or_create(code=code, defaults={"title": title})
            VisitDiagnosis.objects.create(visit=visit, icd10=icd, kind=kind, added_by=doctor)

        def test(name, kind="lab"):
            return TestCatalogue.objects.get_or_create(name=name, defaults={"kind": kind})[0]

        def triage(visit, minutes_ago, complaint, priority="routine", **vitals):
            when = now - datetime.timedelta(minutes=minutes_ago)
            visit.complaint, visit.priority, visit.triaged_by, visit.triaged_at = complaint, priority, nurse, when
            visit.save()
            VitalSigns.objects.create(visit=visit, recorded_by=nurse, recorded_at=when, context="triage", **vitals)

        def entry_for(patient):
            return QueueEntry.objects.filter(patient=patient, queue_date=today).exclude(stage="done").first()

        wanjiru, brian, amina, joseph, grace, samuel, mercy, peter = patients

        # waiting for doctor (Amina has a raised blood pressure; Joseph is urgent)
        for patient, mins, complaint, prio, vit in (
            (amina, 18, "Headache and dizziness for 2 days", "routine",
             dict(temperature="36.8", pulse=88, resp_rate=18, bp_systolic=158, bp_diastolic=98, spo2=97, weight_kg="78", height_cm="160", pain_score=4)),
            (joseph, 24, "Chest tightness since this morning", "urgent",
             dict(temperature="37.1", pulse=104, resp_rate=22, bp_systolic=142, bp_diastolic=90, spo2=95, weight_kg="82", height_cm="175", pain_score=6)),
        ):  # fmt: skip
            e = entry_for(patient)
            if e:
                triage(Visit.for_queue_entry(e), mins, complaint, prio, **vit)

        # with the doctor now (Grace): notes, a diagnosis and a prescription
        e = entry_for(grace)
        if e and doctor:
            v = Visit.for_queue_entry(e)
            triage(v, 25, "Fever and headache for 3 days", temperature="38.6", pulse=96, resp_rate=18,
                   bp_systolic=118, bp_diastolic=76, spo2=98, weight_kg="64", height_cm="165", pain_score=3)  # fmt: skip
            v.doctor = doctor
            v.save()
            ClinicalNote.objects.create(
                visit=v, kind="history", text="Chills at night. No cough or vomiting.", author=doctor
            )
            dx(v, "B50.9", "Malaria, falciparum, unspecified")
            drug = Drug.objects.filter(name="Paracetamol").first()
            if drug:
                Prescription.objects.create(
                    visit=v, drug=drug, dose="1 g", units_per_dose=2, frequency="tds", days=3, prescribed_by=doctor
                )

        # at the lab now (Samuel): a malaria test in progress
        e = entry_for(samuel)
        if e and doctor:
            v = Visit.for_queue_entry(e)
            triage(v, 40, "Fever and body aches", temperature="38.2", pulse=92, resp_rate=19, bp_systolic=120, bp_diastolic=80, spo2=98)  # fmt: skip
            v.doctor = doctor
            v.save()
            Order.objects.create(visit=v, test=test("Malaria test (RDT or slide)"), ordered_by=doctor,
                                 status="in_progress", sample_taken_at=now - datetime.timedelta(minutes=20))  # fmt: skip

        # a past visit for Wanjiru with a resulted test, so "Past visits" has something to show
        past = Visit.objects.create(patient=wanjiru, doctor=doctor, status="closed", outcome="home",
                                    started_at=now - datetime.timedelta(days=88), closed_at=now - datetime.timedelta(days=88),
                                    complaint="Cough and sore throat", triaged_by=nurse, triaged_at=now - datetime.timedelta(days=88))  # fmt: skip
        dx(past, "J06.9", "Acute upper respiratory infection, unspecified", "confirmed")
        po = Order.objects.create(visit=past, test=test("Full blood count"), ordered_by=doctor, status="resulted")
        Result.objects.create(order=po, value="Hb 13.1", unit="g/dL", flag="normal", resulted_by=lab)

        # one inpatient (Mercy, in the female ward) and one discharged earlier (Peter)
        free = Bed.objects.filter(status="free", is_active=True).order_by("ward__name", "number")
        bed = free.filter(ward__name="Female ward").first() or free.first()
        if bed and doctor:
            v = Visit.objects.create(patient=mercy, kind="inpatient", doctor=doctor, outcome="admit",
                                     complaint="Vomiting and abdominal pain for 2 days",
                                     started_at=now - datetime.timedelta(days=1))  # fmt: skip
            VitalSigns.objects.create(visit=v, recorded_by=nurse, context="triage", temperature="37.8", pulse=100,
                                      bp_systolic=110, bp_diastolic=70, spo2=97)  # fmt: skip
            dx(v, "A09", "Diarrhoea and gastroenteritis of presumed infectious origin")
            adm = Admission.objects.create(patient=mercy, visit=v, bed=bed, admitted_by=doctor,
                                           admitted_at=now - datetime.timedelta(days=1),
                                           reason="Dehydration from gastroenteritis, needs IV fluids")  # fmt: skip
            BedMove.objects.create(admission=adm, to_bed=bed, moved_by=doctor, moved_at=adm.admitted_at)
            bed.status = "occupied"
            bed.save()
            VitalSigns.objects.create(visit=v, recorded_by=nurse, context="ward", temperature="37.2", pulse=84,
                                      bp_systolic=112, bp_diastolic=72, spo2=98)  # fmt: skip
            ClinicalNote.objects.create(
                visit=v, kind="progress", text="Tolerating oral fluids. IV fluids continuing.", author=nurse
            )

        free = Bed.objects.filter(status="free", is_active=True).order_by("ward__name", "number")
        old_bed = free.filter(ward__name="Male ward").first() or free.first()
        if old_bed and doctor:
            start, end = now - datetime.timedelta(days=9), now - datetime.timedelta(days=6)
            v = Visit.objects.create(patient=peter, kind="inpatient", doctor=doctor, outcome="admit", status="closed",
                                     started_at=start, closed_at=end, complaint="Fever and cough")  # fmt: skip
            dx(v, "J18.9", "Pneumonia, unspecified", "confirmed")
            adm = Admission.objects.create(patient=peter, visit=v, bed=old_bed, admitted_by=doctor, admitted_at=start,
                                           reason="Pneumonia", status="discharged", discharged_at=end, discharged_by=doctor,
                                           condition_at_discharge="improved",
                                           summary="Treated with IV ceftriaxone for 3 days. Fever settled.",
                                           discharge_medication="Amoxicillin 500 mg three times a day for 5 days",
                                           follow_up="Review in clinic in 1 week")  # fmt: skip
            BedMove.objects.create(admission=adm, to_bed=old_bed, moved_by=doctor, moved_at=start)
