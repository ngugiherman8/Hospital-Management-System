from django.contrib import admin

from .models import GoNoGo, Issue, LegacyRecord, PilotDay, Training


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("title", "severity", "status", "department", "reported_on", "due_on")
    list_filter = ("severity", "status", "department")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LegacyRecord)
class LegacyRecordAdmin(admin.ModelAdmin):
    list_display = ("legacy_id", "legacy_number", "patient", "source", "dob_estimated")
    search_fields = ("legacy_id", "legacy_number", "patient__last_name")
    raw_id_fields = ("patient",)


admin.site.register(PilotDay)
admin.site.register(GoNoGo)
admin.site.register(Training)
