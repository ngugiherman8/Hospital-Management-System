from django import forms

from apps.pharmacy_rx.models import Drug

from .models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, StockBatch, StockMovement, Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "email"]


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)


class POLineForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderLine
        fields = ["drug", "quantity", "unit_cost"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["drug"].queryset = Drug.objects.filter(is_active=True)


class ReceiptForm(forms.ModelForm):
    class Meta:
        model = GoodsReceipt
        fields = ["supplier", "supplier_invoice"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)


class ReceiptLineForm(forms.Form):
    drug = forms.ModelChoiceField(queryset=Drug.objects.none())
    batch_number = forms.CharField(max_length=60)
    expiry_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    quantity = forms.IntegerField(min_value=1)
    unit_cost = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, initial=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["drug"].queryset = Drug.objects.filter(is_active=True)


class AdjustForm(forms.Form):
    batch = forms.ModelChoiceField(queryset=StockBatch.objects.none())
    kind = forms.ChoiceField(
        choices=[(StockMovement.Kind.ADJUST, "Correction"), (StockMovement.Kind.EXPIRED, "Expired or written off")]
    )
    quantity = forms.IntegerField(min_value=1, help_text="How many units. A correction can add or take away below.")
    direction = forms.ChoiceField(choices=[("-", "Take away"), ("+", "Add")], initial="-")
    reason = forms.CharField(max_length=300)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = StockBatch.objects.select_related("drug")

    def signed_quantity(self):
        d = self.cleaned_data
        if d["kind"] == StockMovement.Kind.EXPIRED:
            return -d["quantity"]
        return d["quantity"] if d["direction"] == "+" else -d["quantity"]
