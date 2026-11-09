from django.contrib import admin

from .models import Invoice, InvoiceLine, MpesaRequest, Payer, Payment, Price, ServiceItem


class PriceInline(admin.TabularInline):
    model = Price
    extra = 0


@admin.register(ServiceItem)
class ServiceItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")
    inlines = [PriceInline]


@admin.register(Payer)
class PayerAdmin(admin.ModelAdmin):
    list_display = ("name", "payer_type", "is_active")
    list_filter = ("payer_type", "is_active")


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money records are never changed or deleted from the admin site. Use the cashier screens."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    list_display = ("number", "patient", "payer", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("number", "patient__last_name")


@admin.register(InvoiceLine)
class InvoiceLineAdmin(ReadOnlyAdmin):
    list_display = ("invoice", "description", "amount", "is_removed")


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("receipt_number", "invoice", "method", "amount", "received_at")
    list_filter = ("method",)


@admin.register(MpesaRequest)
class MpesaRequestAdmin(ReadOnlyAdmin):
    list_display = ("checkout_request_id", "invoice", "amount", "status", "created_at")
