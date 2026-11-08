import re
from decimal import Decimal

from django import forms

from .models import Outcome, Priority, VitalSigns

RANGES = {  # field: (min, max, label)
    "temperature": (25, 45, "Temperature"),
    "pulse": (20, 250, "Pulse"),
    "resp_rate": (4, 80, "Breathing"),
    "spo2": (40, 100, "Oxygen saturation"),
    "blood_sugar": (0.5, 60, "Blood sugar"),
    "weight_kg": (0.3, 400, "Weight"),
    "height_cm": (20, 250, "Height"),
    "pain_score": (0, 10, "Pain score"),
}


class VitalsForm(forms.Form):
    temperature = forms.DecimalField(required=False, max_digits=4, decimal_places=1, label="Temperature (°C)")
    pulse = forms.IntegerField(required=False, label="Pulse (beats/min)")
    resp_rate = forms.IntegerField(required=False, label="Breathing (per min)")
    blood_pressure = forms.CharField(
        required=False,
        max_length=12,
        label="Blood pressure (mmHg)",
        widget=forms.TextInput(attrs={"placeholder": "118 / 76"}),
    )
    spo2 = forms.IntegerField(required=False, label="Oxygen saturation (%)")
    blood_sugar = forms.DecimalField(
        required=False,
        max_digits=4,
        decimal_places=1,
        label="Blood sugar (mmol/L, if needed)",
        widget=forms.NumberInput(attrs={"placeholder": "Not taken"}),
    )
    weight_kg = forms.DecimalField(required=False, max_digits=5, decimal_places=1, label="Weight (kg)")
    height_cm = forms.DecimalField(required=False, max_digits=4, decimal_places=1, label="Height (cm)")
    pain_score = forms.IntegerField(
        required=False, min_value=0, max_value=10, widget=forms.HiddenInput, label="Pain score (0 = none, 10 = worst)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("temperature", "blood_sugar", "weight_kg", "height_cm"):
            self.fields[name].widget.attrs["step"] = "0.1"

    def clean(self):
        data = super().clean()
        for name, (lo, hi, label) in RANGES.items():
            v = data.get(name)
            if v is not None and not (Decimal(str(lo)) <= Decimal(str(v)) <= Decimal(str(hi))):
                self.add_error(name, f"{label} should be between {lo} and {hi}. Check the reading.")
        bp = (data.get("blood_pressure") or "").strip()
        data["bp_systolic"] = data["bp_diastolic"] = None
        if bp:
            m = re.fullmatch(r"(\d{2,3})\s*[/\\-]\s*(\d{2,3})", bp)
            if not m:
                self.add_error("blood_pressure", "Write it as 120 / 80.")
            else:
                s, d = int(m.group(1)), int(m.group(2))
                if not (40 <= s <= 300 and 20 <= d <= 200 and s > d):
                    self.add_error("blood_pressure", "Check the blood pressure reading.")
                else:
                    data["bp_systolic"], data["bp_diastolic"] = s, d
        values = [data.get(k) for k in RANGES] + [data["bp_systolic"]]
        if not self.errors and all(v in (None, "") for v in values):
            raise forms.ValidationError("Enter at least one reading.")
        return data

    def to_vitals(self, instance: VitalSigns | None = None, **extra) -> VitalSigns:
        d = self.cleaned_data
        vs = instance or VitalSigns(**extra)
        for name in (
            "temperature",
            "pulse",
            "resp_rate",
            "bp_systolic",
            "bp_diastolic",
            "spo2",
            "blood_sugar",
            "weight_kg",
            "height_cm",
            "pain_score",
        ):
            setattr(vs, name, d.get(name))
        vs.save()
        return vs

    @classmethod
    def initial_from(cls, vs: VitalSigns):
        return {
            "temperature": vs.temperature,
            "pulse": vs.pulse,
            "resp_rate": vs.resp_rate,
            "blood_pressure": vs.bp,
            "spo2": vs.spo2,
            "blood_sugar": vs.blood_sugar,
            "weight_kg": vs.weight_kg,
            "height_cm": vs.height_cm,
            "pain_score": vs.pain_score,
        }


class TriageForm(VitalsForm):
    complaint = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}), label="Main complaint, in the patient's words"
    )
    duration = forms.CharField(max_length=60, required=False, label="How long?")
    priority = forms.ChoiceField(choices=Priority.choices, initial=Priority.ROUTINE, widget=forms.RadioSelect)


class DiagnosisAddForm(forms.Form):
    icd10 = forms.IntegerField()
    kind = forms.ChoiceField(
        choices=[("provisional", "Provisional"), ("confirmed", "Confirmed")], initial="provisional", required=False
    )


class NoteForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Progress note")


class AmendForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Corrected note")
    reason = forms.CharField(max_length=200, label="Why is this being amended?")


class ConsultForm(forms.Form):
    history = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    examination = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Findings"}))
    plan = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Plan"}))
    outcome = forms.ChoiceField(choices=Outcome.choices, required=False, widget=forms.RadioSelect)
    review_in_days = forms.TypedChoiceField(
        required=False,
        coerce=int,
        empty_value=None,
        choices=[
            ("", "No review needed"),
            (1, "Tomorrow"),
            (3, "In 3 days"),
            (7, "In 1 week"),
            (14, "In 2 weeks"),
            (30, "In 1 month"),
        ],
    )
    referred_to = forms.CharField(required=False, max_length=160, label="Refer to (facility)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["form"] = "mainform"  # inputs sit outside the <form> tag on the page


class BreakGlassForm(forms.Form):
    reason = forms.CharField(
        min_length=10,
        max_length=300,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Why do you need this record now?",
    )
