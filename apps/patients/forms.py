from django import forms
from django.utils import timezone

from apps.appointments.models import Clinic

from .models import Patient


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        kwargs.setdefault("format", "%Y-%m-%d")
        super().__init__(**kwargs)


class PatientForm(forms.ModelForm):
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
            "residence",
            "email",
            "kin_name",
            "kin_relationship",
            "kin_phone",
            "allergies",
            "payer",
            "payer_name",
            "member_number",
        ]
        widgets = {
            "date_of_birth": DateInput(),
            "allergies": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "phone": "Phone (also used for M-Pesa and SMS reminders)",
            "residence": "Area of residence",
            "kin_name": "Next of kin name",
            "kin_relationship": "Relationship",
            "kin_phone": "Next of kin phone",
            "allergies": "Known allergies (optional)",
            "member_number": "Member number",
        }

    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        if dob > timezone.localdate():
            raise forms.ValidationError("Date of birth cannot be in the future.")
        return dob

    def clean(self):
        data = super().clean()
        if not data.get("phone") and not data.get("kin_phone"):
            self.add_error("phone", "Enter the patient's phone, or the next of kin's phone.")
        needs_member_number = (Patient.Payer.SHA, Patient.Payer.INSURER, Patient.Payer.CORPORATE)
        if data.get("payer") in needs_member_number and not data.get("member_number"):
            self.add_error("member_number", "Enter the member number for this payer.")
        if data.get("payer") in (Patient.Payer.INSURER, Patient.Payer.CORPORATE) and not data.get("payer_name"):
            self.add_error("payer_name", "Enter the insurer or company name.")
        return data


class RegistrationExtras(forms.Form):
    """Options shown beside the registration form."""

    confirm_new = forms.BooleanField(required=False)
    add_to_queue = forms.BooleanField(required=False, initial=True, label="Add to today's queue")
    clinic = forms.ChoiceField(choices=Clinic.choices, required=False)
