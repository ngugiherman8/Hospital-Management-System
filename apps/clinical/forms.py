from django import forms

from .models import Consultation, Diagnosis, NoteVersion, TriageRecord


class TriageForm(forms.ModelForm):
    class Meta:
        model = TriageRecord
        fields = [
            "temperature", "pulse", "resp_rate", "bp_systolic", "bp_diastolic", "spo2",
            "weight_kg", "height_cm", "pain", "complaint", "priority",
        ]  # fmt: skip
        widgets = {"complaint": forms.Textarea(attrs={"rows": 2})}


class NoteForm(forms.ModelForm):
    class Meta:
        model = NoteVersion
        fields = ["history", "examination", "plan"]
        widgets = {f: forms.Textarea(attrs={"rows": 3}) for f in fields}

    def clean(self):
        data = super().clean()
        if not any((data.get(f) or "").strip() for f in self.Meta.fields):
            raise forms.ValidationError("Write something before saving the note.")
        return data


class DiagnosisForm(forms.ModelForm):
    class Meta:
        model = Diagnosis
        fields = ["icd10_code", "name", "certainty"]


class OutcomeForm(forms.ModelForm):
    class Meta:
        model = Consultation
        fields = ["outcome", "refer_to", "review_date"]
        widgets = {"review_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        if data.get("outcome") == Consultation.Outcome.REFER and not (data.get("refer_to") or "").strip():
            self.add_error("refer_to", "Say where the patient is referred to.")
        return data
