from django.contrib import admin

from .models import ContactSchedule


@admin.register(ContactSchedule)
class ContactScheduleAdmin(admin.ModelAdmin):
    list_display = ("care", "contact_number", "due_at", "grace_days", "note")
    list_filter = ("care",)
