from django.contrib import admin

from .models import Drug


@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("name", "strength", "form", "default_route", "price", "is_active")
    search_fields = ("name",)
    list_filter = ("form", "is_active")
