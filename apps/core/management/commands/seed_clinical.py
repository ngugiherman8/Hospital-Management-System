"""Starter lab tests, drugs and wards so the clinical screens work on a new server.

These are placeholders to be replaced with the hospital's own catalogue, formulary and ward list.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.dental.models import DentalProcedure
from apps.imaging.models import ImagingItem, ImagingModality
from apps.maternity.models import ContactSchedule
from apps.orders.models import TestCatalogue
from apps.pharmacy_rx.models import Drug
from apps.wards.models import Bed, Ward

TESTS = [
    ("MAL-RDT", "Malaria rapid test", "Blood", "", None, None, "Negative"),
    ("MAL-BS", "Malaria blood slide", "Blood", "", None, None, "No parasites seen"),
    ("HB", "Haemoglobin", "Blood", "g/dL", 12, 17, "12 to 17 g/dL"),
    ("FBC", "Full blood count", "Blood", "", None, None, "See lab ranges"),
    ("RBS", "Random blood sugar", "Blood", "mmol/L", 3.9, 7.8, "3.9 to 7.8 mmol/L"),
    ("HIV", "HIV rapid test", "Blood", "", None, None, "Non-reactive"),
    ("UA", "Urinalysis", "Urine", "", None, None, "See lab ranges"),
    ("PREG", "Pregnancy test (urine)", "Urine", "", None, None, "Negative"),
    ("STOOL", "Stool microscopy", "Stool", "", None, None, "No ova or parasites"),
    ("WIDAL", "Widal test", "Blood", "", None, None, "Negative"),
]
DRUGS = [
    ("Paracetamol", "500 mg", "Tablet", "Oral"),
    ("Amoxicillin", "500 mg", "Capsule", "Oral"),
    ("Artemether/Lumefantrine", "20/120 mg", "Tablet", "Oral"),
    ("Ibuprofen", "400 mg", "Tablet", "Oral"),
    ("Metronidazole", "400 mg", "Tablet", "Oral"),
    ("Ciprofloxacin", "500 mg", "Tablet", "Oral"),
    ("Cotrimoxazole", "480 mg", "Tablet", "Oral"),
    ("Oral rehydration salts", "", "Sachet", "Oral"),
    ("Zinc sulfate", "20 mg", "Dispersible tablet", "Oral"),
    ("Amlodipine", "5 mg", "Tablet", "Oral"),
    ("Metformin", "500 mg", "Tablet", "Oral"),
    ("Salbutamol", "100 mcg", "Inhaler", "Inhaled"),
    ("Ceftriaxone", "1 g", "Injection", "IV or IM"),
    ("Diclofenac", "50 mg", "Tablet", "Oral"),
    ("Penicillin V", "250 mg", "Tablet", "Oral"),
]
MODALITIES = [
    ("X-ray", "DX", "X-ray room"),
    ("Ultrasound", "US", "Ultrasound room"),
    ("CT", "CT", "CT room"),
    ("MRI", "MR", "MRI room"),
    ("Mammography", "MG", "Mammography room"),
    ("Fluoroscopy", "RF", "Fluoroscopy room"),
    ("Dental X-ray", "DX", "Dental X-ray room"),
]
# code, name, modality, body part, radiation, contrast
STUDIES = [
    ("XR-CHEST", "Chest X-ray", "X-ray", "Chest", True, False),
    ("XR-ABD", "Abdomen X-ray", "X-ray", "Abdomen", True, False),
    ("XR-LIMB", "Limb X-ray", "X-ray", "Limb", True, False),
    ("US-ABD", "Abdominal ultrasound", "Ultrasound", "Abdomen", False, False),
    ("US-OBS", "Obstetric ultrasound", "Ultrasound", "Pelvis", False, False),
    ("CT-HEAD", "CT head", "CT", "Head", True, False),
    ("CT-ABD-C", "CT abdomen with contrast", "CT", "Abdomen", True, True),
    ("MRI-BRAIN", "MRI brain", "MRI", "Head", False, False),
    ("MG-BILAT", "Mammogram, both breasts", "Mammography", "Breast", True, False),
    ("DX-PA", "Dental periapical X-ray", "Dental X-ray", "Teeth", True, False),
    ("DX-BW", "Dental bitewing X-ray", "Dental X-ray", "Teeth", True, False),
    ("DX-PAN", "Dental panoramic X-ray", "Dental X-ray", "Jaws and teeth", True, False),
]
DENTAL = [
    ("EXAM", "Dental examination"),
    ("SCALE", "Scaling and polishing"),
    ("FILL", "Filling"),
    ("EXTRACT", "Tooth extraction"),
    ("RCT", "Root canal treatment"),
    ("CROWN", "Crown"),
]
# care, number, due (weeks of pregnancy or days after birth)
SCHEDULE = [
    ("antenatal", 1, 12), ("antenatal", 2, 20), ("antenatal", 3, 26), ("antenatal", 4, 30),
    ("antenatal", 5, 34), ("antenatal", 6, 36), ("antenatal", 7, 38), ("antenatal", 8, 40),
    ("postnatal", 1, 1), ("postnatal", 2, 3), ("postnatal", 3, 14), ("postnatal", 4, 42),
]  # fmt: skip
WARDS = [("Male ward", "general", 6), ("Female ward", "general", 6), ("Paediatric ward", "paediatric", 4)]


class Command(BaseCommand):
    help = "Add placeholder tests, drugs and wards (safe to run again)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            self.stdout.write("Adding placeholder catalogue entries; replace them with the hospital's own lists.")
        for code, name, sample, unit, low, high, normal in TESTS:
            TestCatalogue.objects.get_or_create(
                code=code,
                defaults=dict(
                    name=name, sample_type=sample, unit=unit, ref_low=low, ref_high=high, normal_range=normal
                ),
            )
        for name, strength, form, route in DRUGS:
            Drug.objects.get_or_create(generic_name=name, strength=strength, form=form, defaults={"route": route})
        for name, kind, beds in WARDS:
            ward, _ = Ward.objects.get_or_create(name=name, defaults={"kind": kind})
            for n in range(1, beds + 1):
                Bed.objects.get_or_create(ward=ward, number=str(n))
        for name, code, room in MODALITIES:
            ImagingModality.objects.get_or_create(name=name, defaults={"dicom_code": code, "room": room})
        for code, name, modality, body, radiation, contrast in STUDIES:
            test, _ = TestCatalogue.objects.get_or_create(code=code, defaults={"name": name, "kind": "imaging"})
            ImagingItem.objects.get_or_create(
                test=test,
                defaults={
                    "modality": ImagingModality.objects.get(name=modality),
                    "body_part": body,
                    "uses_radiation": radiation,
                    "uses_contrast": contrast,
                },
            )
        for code, name in DENTAL:
            DentalProcedure.objects.get_or_create(code=code, defaults={"name": name})
        for care, number, due in SCHEDULE:
            ContactSchedule.objects.get_or_create(
                care=care,
                contact_number=number,
                defaults={"due_at": due, "grace_days": 7 if care == "antenatal" else 3},
            )
        self.stdout.write(self.style.SUCCESS("Placeholder tests, drugs and wards are in place."))
