from decimal import Decimal

from django import forms

from .models import Payment, ServiceItem

MONEY_INPUT = {"step": "0.01", "min": "0.01"}


class AddLineForm(forms.Form):
    service = forms.ModelChoiceField(ServiceItem.objects.none(), label="Service")
    quantity = forms.DecimalField(min_value=Decimal("0.01"), max_digits=8, decimal_places=2, initial=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = ServiceItem.objects.filter(is_active=True)


class PaymentForm(forms.Form):
    method = forms.ChoiceField(choices=Payment.Method.choices, initial="cash")
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                                widget=forms.NumberInput(attrs=MONEY_INPUT))  # fmt: skip
    mpesa_code = forms.CharField(required=False, max_length=20, label="M-Pesa code")
    reference = forms.CharField(required=False, max_length=120, label="Reference (card slip, cheque, etc.)")
    is_deposit = forms.BooleanField(required=False, label="Deposit (money taken in advance)")


class MpesaPromptForm(forms.Form):
    phone = forms.CharField(max_length=20, label="Patient's M-Pesa phone")
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                                widget=forms.NumberInput(attrs=MONEY_INPUT))  # fmt: skip


class RefundForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                                widget=forms.NumberInput(attrs=MONEY_INPUT))  # fmt: skip
    method = forms.ChoiceField(choices=Payment.Method.choices, initial="cash", label="Paid back by")
    reason = forms.CharField(max_length=200)


class CreditForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
                                widget=forms.NumberInput(attrs=MONEY_INPUT))  # fmt: skip
    reason = forms.CharField(max_length=200)


class VoidForm(forms.Form):
    reason = forms.CharField(max_length=200)
