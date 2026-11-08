from django import forms

from .models import Admission, Bed


def free_beds():
    taken = Admission.objects.filter(status="admitted").values_list("bed_id", flat=True)
    return (
        Bed.objects.filter(is_active=True, ward__is_active=True, status=Bed.Status.FREE)
        .exclude(pk__in=taken)
        .select_related("ward")
    )


class AdmitForm(forms.Form):
    bed = forms.ModelChoiceField(Bed.objects.none(), label="Ward and bed")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Reason for admission")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"].queryset = free_beds()


class TransferForm(forms.Form):
    bed = forms.ModelChoiceField(Bed.objects.none(), label="Move to")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"].queryset = free_beds()


class DischargeForm(forms.Form):
    condition_at_discharge = forms.ChoiceField(choices=Admission.Condition.choices, label="Condition at discharge")
    summary = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), label="Course in hospital and treatment given")
    discharge_medication = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Medicines to take home"
    )
    follow_up = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Follow-up plan")
