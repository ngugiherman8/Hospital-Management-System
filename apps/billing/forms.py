from django import forms

from .models import Claim, Invoice, Payment, ServiceItem


class AddLineForm(forms.Form):
    service = forms.ModelChoiceField(queryset=ServiceItem.objects.none())
    quantity = forms.IntegerField(min_value=1, initial=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = ServiceItem.objects.filter(is_active=True)


class PaymentForm(forms.Form):
    method = forms.ChoiceField(choices=Payment.Method.choices)
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    mpesa_code = forms.CharField(max_length=20, required=False, label="M-Pesa code")
    reference = forms.CharField(max_length=100, required=False, label="Card slip or reference")

    def __init__(self, *args, show_insurer=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not show_insurer:  # insurer money arrives through a claim, not the till
            self.fields["method"].choices = [c for c in Payment.Method.choices if c[0] != "insurer"]


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=300)


class RefundForm(ReasonForm):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01)
    method = forms.ChoiceField(choices=[c for c in Payment.Method.choices if c[0] != "insurer"], initial="cash")


class PayerForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["payer_kind", "payer_name", "member_number"]


class CashUpForm(forms.Form):
    cash = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, initial=0)
    mpesa = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, initial=0, label="M-Pesa")
    card = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, initial=0)
    deposit = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, initial=0)
    explanation = forms.CharField(max_length=300, required=False, label="If the count is out, why?")


class ClaimCorrectForm(forms.Form):
    member_number = forms.CharField(max_length=60)
    diagnoses = forms.CharField(max_length=300)


class ClaimAmountForm(forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reference = forms.CharField(max_length=100, required=False)


class ClaimFilterForm(forms.Form):
    month = forms.CharField(required=False, max_length=7, label="Month (YYYY-MM)")
    status = forms.ChoiceField(choices=[("", "Any status")] + Claim.Status.choices, required=False)
