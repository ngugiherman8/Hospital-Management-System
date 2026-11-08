from decimal import Decimal

from django import forms

from .models import Drug, Prescription


class PrescriptionForm(forms.Form):
    drug = forms.ModelChoiceField(Drug.objects.none())
    dose = forms.CharField(max_length=40, label="Dose", widget=forms.TextInput(attrs={"placeholder": "e.g. 1 g"}))
    units_per_dose = forms.DecimalField(
        min_value=Decimal("0.1"),
        max_value=Decimal("100"),
        decimal_places=1,
        initial=1,
        label="Units each time (tablets / ml)",
    )
    route = forms.ChoiceField(choices=[("", "Drug default")] + list(Drug.Route.choices), required=False)
    frequency = forms.ChoiceField(choices=Prescription.Frequency.choices, initial="tds", label="How often")
    days = forms.IntegerField(min_value=1, max_value=90, initial=3, label="Days")
    override_allergy = forms.BooleanField(required=False, label="I have read the allergy warning and still prescribe")
    override_reason = forms.CharField(required=False, max_length=200, label="Reason")

    def __init__(self, *args, patient=None, visit=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient, self.visit = patient, visit
        self.fields["drug"].queryset = Drug.objects.filter(is_active=True)
        self.allergy_conflict = ""

    def clean(self):
        data = super().clean()
        drug = data.get("drug")
        if not drug:
            return data
        if self.visit and self.visit.prescriptions.filter(drug=drug).exclude(status="cancelled").exists():
            self.add_error("drug", "This drug is already prescribed for this visit.")
        if self.patient:
            self.allergy_conflict = drug.allergy_conflict(self.patient.allergies)
            if self.allergy_conflict and not (
                data.get("override_allergy") and (data.get("override_reason") or "").strip()
            ):
                raise forms.ValidationError(
                    f"Allergy warning: the patient's record mentions '{self.allergy_conflict}', "
                    f"which matches {drug.label}. To prescribe anyway, tick the box and give a reason."
                )
        return data
