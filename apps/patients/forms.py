import datetime

from django import forms
from django.core.exceptions import ValidationError

from .models import IdType, InsuranceCover, Patient, PayerType, normalize_phone


class PatientForm(forms.ModelForm):
    payer_type = forms.ChoiceField(
        choices=PayerType.choices, initial=PayerType.CASH, widget=forms.RadioSelect, label="How will the patient pay?"
    )
    insurer_name = forms.CharField(required=False, max_length=120, label="Insurer or company")
    member_number = forms.CharField(required=False, max_length=60, label="Member number")

    class Meta:
        model = Patient
        fields = [
            "first_name",
            "last_name",
            "date_of_birth",
            "sex",
            "id_type",
            "id_number",
            "phone",
            "area_of_residence",
            "next_of_kin_name",
            "next_of_kin_relationship",
            "next_of_kin_phone",
            "allergies",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        labels = {
            "phone": "Phone (also used for M-Pesa and SMS reminders)",
            "area_of_residence": "Area of residence",
            "next_of_kin_name": "Next of kin name",
            "next_of_kin_relationship": "Relationship",
            "next_of_kin_phone": "Next of kin phone",
            "allergies": "Known allergies (optional)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            cover = self.instance.covers.filter(is_primary=True).first()
            if cover:
                self.initial.update(
                    payer_type=cover.payer_type, insurer_name=cover.insurer_name, member_number=cover.member_number
                )

    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        if dob > datetime.date.today():
            raise ValidationError("Date of birth cannot be in the future.")
        if dob.year < 1890:
            raise ValidationError("Check the date of birth.")
        return dob

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        n = normalize_phone(phone)
        if not (n.startswith("254") and len(n) == 12):
            raise ValidationError("Enter a valid Kenyan mobile number, e.g. 0712 345 678.")
        return phone

    def clean(self):
        data = super().clean()
        id_type, id_number = data.get("id_type"), (data.get("id_number") or "").strip()
        if id_type and id_type != IdType.NONE and not id_number:
            self.add_error("id_number", "Enter the ID number, or choose 'No ID'.")
        if id_type and id_type != IdType.NONE and id_number:
            clash = Patient.objects.filter(id_type=id_type, id_number=id_number)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("id_number", "A patient with this ID already exists.")
        payer = data.get("payer_type")
        if payer in (PayerType.SHA, PayerType.PRIVATE, PayerType.CORPORATE) and not data.get("member_number"):
            self.add_error("member_number", "Member number is required for this payer.")
        if payer in (PayerType.PRIVATE, PayerType.CORPORATE) and not data.get("insurer_name"):
            self.add_error("insurer_name", "Enter the insurer or company name.")
        return data

    def save_cover(self, patient):
        d = self.cleaned_data
        cover = patient.covers.filter(is_primary=True).first() or InsuranceCover(patient=patient)
        cover.payer_type = d["payer_type"]
        cover.insurer_name = d.get("insurer_name", "")
        cover.member_number = d.get("member_number", "")
        cover.is_primary = True
        cover.save()
        return cover
