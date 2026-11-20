from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "visit_date", "queue_number", "clinic", "doctor", "status")
    list_filter = ("status", "clinic", "visit_date")
    search_fields = ("patient__first_name", "patient__last_name", "patient__hospital_number")
