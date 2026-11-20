import datetime

from django import forms
from django.utils import timezone

from .models import (
    DANGER_SIGNS,
    POSTNATAL_DANGER_SIGNS,
    AntenatalContact,
    DeliverySummary,
    PostnatalContact,
    PregnancyEpisode,
)

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class PregnancyForm(forms.ModelForm):
    lmp = forms.DateField(widget=DATE, label="Last menstrual period")
    edd = forms.DateField(widget=DATE, required=False, label="Due date (leave empty to work out from the LMP)")

    class Meta:
        model = PregnancyEpisode
        fields = ["lmp", "edd", "previous_pregnancies", "previous_births", "blood_group", "risk_factors"]
        widgets = {"risk_factors": forms.Textarea(attrs={"rows": 2})}

    def clean_lmp(self):
        lmp = self.cleaned_data["lmp"]
        today = timezone.localdate()
        if lmp > today:
            raise forms.ValidationError("The last menstrual period cannot be in the future.")
        if lmp < today - datetime.timedelta(days=330):
            raise forms.ValidationError("That is more than 47 weeks ago. Check the date.")
        return lmp


class ContactForm(forms.ModelForm):
    danger_signs = forms.MultipleChoiceField(
        choices=DANGER_SIGNS, required=False, widget=forms.CheckboxSelectMultiple, label="Danger signs"
    )
    date = forms.DateField(widget=DATE, initial=timezone.localdate)
    next_visit = forms.DateField(widget=DATE, required=False)

    class Meta:
        model = AntenatalContact
        fields = [
            "date", "weight_kg", "bp_systolic", "bp_diastolic", "fundal_height_cm", "fetal_heartbeat",
            "presentation", "urine_protein", "haemoglobin", "hiv_status", "danger_signs",
            "medicines_given", "birth_plan", "next_visit", "referral",
        ]  # fmt: skip
        widgets = {"birth_plan": forms.Textarea(attrs={"rows": 2})}

    def clean_danger_signs(self):
        return list(self.cleaned_data["danger_signs"])


class DeliveryForm(forms.ModelForm):
    delivered_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
        label="Date and time of birth",
    )
    baby_sex = forms.ChoiceField(choices=[("", "Select"), ("F", "Female"), ("M", "Male")], required=False)
    baby_name = forms.CharField(
        max_length=100, required=False, help_text="Leave empty to name the record 'Baby of ...'"
    )

    class Meta:
        model = DeliverySummary
        fields = [
            "delivered_at", "place", "mode", "attended_by", "mother_outcome", "baby_outcome",
            "complications", "birth_weight_kg", "feeding_notes",
        ]  # fmt: skip
        widgets = {"complications": forms.Textarea(attrs={"rows": 2})}

    def clean_delivered_at(self):
        value = self.cleaned_data["delivered_at"]
        if value > timezone.now() + datetime.timedelta(minutes=5):
            raise forms.ValidationError("The birth cannot be in the future.")
        return value


class PostnatalForm(forms.ModelForm):
    danger_signs = forms.MultipleChoiceField(
        choices=POSTNATAL_DANGER_SIGNS, required=False, widget=forms.CheckboxSelectMultiple, label="Danger signs"
    )
    date = forms.DateField(widget=DATE, initial=timezone.localdate)

    class Meta:
        model = PostnatalContact
        fields = ["date", "weight_kg", "bp_systolic", "bp_diastolic", "findings", "danger_signs"]
        widgets = {"findings": forms.Textarea(attrs={"rows": 3})}

    def clean_danger_signs(self):
        return list(self.cleaned_data["danger_signs"])
