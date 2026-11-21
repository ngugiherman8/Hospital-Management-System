from django.contrib import admin

from apps.core.appendonly import ReadOnlyAdmin

from .models import CashUp, Claim, ClaimLine, Invoice, InvoiceLine, ItemPrice, MpesaPrompt, Payment, ServiceItem


class PriceInline(admin.TabularInline):
    model = ItemPrice
    extra = 0


@admin.register(ServiceItem)
class ServiceItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")
    inlines = [PriceInline]


class LineInline(ReadOnlyAdmin, admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("number", "patient", "status", "payer_kind", "created_at")
    list_filter = ("status", "payer_kind")
    inlines = [LineInline]


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("receipt_number", "invoice", "method", "amount", "received_by", "created_at")
    list_filter = ("method",)


@admin.register(CashUp)
class CashUpAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("day", "cashier", "difference")


class ClaimLineInline(ReadOnlyAdmin, admin.TabularInline):
    model = ClaimLine
    extra = 0


@admin.register(Claim)
class ClaimAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("number", "payer_kind", "status", "batch", "amount_claimed")
    list_filter = ("status", "payer_kind", "batch")
    inlines = [ClaimLineInline]


@admin.register(MpesaPrompt)
class MpesaPromptAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("invoice", "phone", "amount", "status", "mpesa_code", "created_at")
    list_filter = ("status",)
