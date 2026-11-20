from django.contrib import admin

from .models import Drug


@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("generic_name", "strength", "form", "route", "price", "is_active")
    list_filter = ("is_active", "form")
    search_fields = ("generic_name",)
