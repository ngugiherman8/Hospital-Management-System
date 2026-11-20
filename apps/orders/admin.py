from django.contrib import admin

from .models import TestCatalogue


@admin.register(TestCatalogue)
class TestCatalogueAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "sample_type", "price", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("code", "name")
