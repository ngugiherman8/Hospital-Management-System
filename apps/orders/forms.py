from django import forms

from .models import Result, TestCatalogue


class OrderForm(forms.Form):
    test = forms.ModelChoiceField(TestCatalogue.objects.none(), label="Test or procedure")
    urgent = forms.BooleanField(required=False, label="Urgent")
    clinical_notes = forms.CharField(required=False, max_length=200, label="Reason or question for the lab")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["test"].queryset = TestCatalogue.objects.filter(is_active=True)


class ResultForm(forms.Form):
    value = forms.CharField(
        max_length=200, label="Result", widget=forms.TextInput(attrs={"placeholder": "e.g. Positive"})
    )
    unit = forms.CharField(required=False, max_length=30)
    flag = forms.ChoiceField(choices=Result.Flag.choices, label="Overall")
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class CorrectionForm(ResultForm):
    comment = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Why is this being corrected?")
