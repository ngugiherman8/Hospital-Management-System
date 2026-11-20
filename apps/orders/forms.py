from django import forms

from .models import OrderItem, Result, TestCatalogue


class OrderForm(forms.Form):
    tests = forms.ModelMultipleChoiceField(
        queryset=TestCatalogue.objects.none(), widget=forms.CheckboxSelectMultiple, label="Tests"
    )
    note = forms.CharField(required=False, max_length=300, label="Clinical note for the lab")

    def __init__(self, *args, kind=TestCatalogue.Kind.LAB, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tests"].queryset = TestCatalogue.objects.filter(kind=kind, is_active=True)


class ResultForm(forms.Form):
    value = forms.CharField(max_length=200)
    unit = forms.CharField(max_length=30, required=False)
    flag = forms.ChoiceField(
        choices=[("", "Work out from normal range")] + Result.Flag.choices, required=False, label="Flag"
    )
    comment = forms.CharField(max_length=300, required=False)


__all__ = ["OrderForm", "ResultForm", "OrderItem"]
