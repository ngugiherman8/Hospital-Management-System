from django.contrib import admin

from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("hospital_number", "full_name", "date_of_birth", "phone", "payer", "is_active")
    search_fields = ("hospital_number", "first_name", "last_name", "phone", "id_number")
    list_filter = ("payer", "is_active")
    readonly_fields = ("hospital_number",)
