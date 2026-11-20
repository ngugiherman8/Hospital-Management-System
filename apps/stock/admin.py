from django.contrib import admin

from apps.core.appendonly import ReadOnlyAdmin

from .models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, StockBatch, StockMovement, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "is_active")


class POLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("number", "supplier", "status")
    inlines = [POLineInline]


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("number", "supplier", "received_on")


@admin.register(StockBatch)
class StockBatchAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("drug", "batch_number", "expiry_date", "quantity")


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("created_at", "batch", "kind", "quantity", "user")
    list_filter = ("kind",)
