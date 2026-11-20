from django import forms

from .models import Drug


class PrescribeForm(forms.Form):
    drug = forms.ModelChoiceField(queryset=Drug.objects.none())
    dose = forms.CharField(max_length=80)
    frequency = forms.CharField(max_length=80)
    days = forms.IntegerField(min_value=1, max_value=365)
    quantity = forms.IntegerField(min_value=1)
    acknowledge_allergy = forms.BooleanField(
        required=False, label="I have read the allergy warning and still want to prescribe this drug"
    )

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient = patient
        self.fields["drug"].queryset = Drug.objects.filter(is_active=True)
        from apps.stock.services import on_hand_map

        stock = on_hand_map()  # the prescriber sees what the pharmacy has before choosing
        self.fields["drug"].label_from_instance = lambda d: f"{d} ({stock.get(d.pk, 0)} in stock)"
        self.needs_acknowledgement = False

    def clean(self):
        data = super().clean()
        drug = data.get("drug")
        if drug and self.patient and drug.matches_allergy(self.patient.allergies):
            self.needs_acknowledgement = True
            if not data.get("acknowledge_allergy"):
                raise forms.ValidationError(
                    f"Allergy warning: {self.patient.full_name} has a recorded allergy ({self.patient.allergies}). "
                    "Tick the box to prescribe anyway."
                )
        return data
