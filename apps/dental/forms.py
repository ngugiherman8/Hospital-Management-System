from django import forms

from .models import DentalProcedure, DentalVisit, ToothRecord, TreatmentPlanItem, validate_fdi


class DentalVisitForm(forms.ModelForm):
    class Meta:
        model = DentalVisit
        fields = [
            "complaint", "dental_history", "allergies_note", "has_diabetes", "bleeding_problems",
            "current_medicines", "examination",
        ]  # fmt: skip
        widgets = {
            "dental_history": forms.Textarea(attrs={"rows": 2}),
            "examination": forms.Textarea(attrs={"rows": 3}),
        }


class ToothForm(forms.Form):
    tooth = forms.IntegerField(label="Tooth (FDI number)", validators=[validate_fdi])
    surface = forms.ChoiceField(choices=ToothRecord.Surface.choices)
    condition = forms.ChoiceField(choices=ToothRecord.Condition.choices)


class PlanItemForm(forms.ModelForm):
    tooth = forms.IntegerField(required=False, label="Tooth (FDI number)", validators=[validate_fdi])
    estimate = forms.DecimalField(max_digits=10, decimal_places=2, required=False, min_value=0)

    class Meta:
        model = TreatmentPlanItem
        fields = ["tooth", "procedure", "estimate"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["procedure"].queryset = DentalProcedure.objects.filter(is_active=True)

    def clean(self):
        data = super().clean()
        procedure = data.get("procedure")
        if procedure and data.get("estimate") is None:
            data["estimate"] = procedure.default_price
        return data


class ConsentForm(forms.Form):
    agreed_by = forms.CharField(max_length=150, label="Estimate shown to and agreed by")


class ItemActionForm(forms.Form):
    action = forms.ChoiceField(choices=[("start", "Start"), ("complete", "Complete"), ("decline", "Decline")])
    anaesthetic_note = forms.CharField(max_length=200, required=False)
