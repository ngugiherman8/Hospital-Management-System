from django.contrib import admin

from .models import Order, TestCatalogue


@admin.register(TestCatalogue)
class TestCatalogueAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "sample_type", "normal_range", "price", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "test", "status", "ordered_at")
    list_filter = ("status",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
