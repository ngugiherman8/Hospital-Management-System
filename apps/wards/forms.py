from django import forms

from .models import Bed


class AdmitForm(forms.Form):
    bed = forms.ModelChoiceField(queryset=Bed.objects.none())
    reason = forms.CharField(max_length=300, label="Reason for admission")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"].queryset = Bed.objects.filter(status=Bed.Status.FREE).select_related("ward")


class TransferForm(forms.Form):
    bed = forms.ModelChoiceField(queryset=Bed.objects.none(), label="Move to")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"].queryset = Bed.objects.filter(status=Bed.Status.FREE).select_related("ward")


class DischargeForm(forms.Form):
    summary = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), label="Discharge summary")
