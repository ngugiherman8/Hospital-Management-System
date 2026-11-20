from django.contrib import admin

from .models import DentalProcedure


@admin.register(DentalProcedure)
class DentalProcedureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "default_price", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
