from django.contrib import admin

from . import models as m


@admin.register(m.ICD10Code)
class ICD10Admin(admin.ModelAdmin):
    list_display = ("code", "title")
    search_fields = ("code", "title")


class ReadOnlyAdmin(admin.ModelAdmin):
    """Clinical records are read-only here: they change only through the clinical screens (and are audited)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(m.Visit)
class VisitAdmin(ReadOnlyAdmin):
    list_display = ("patient", "kind", "status", "priority", "started_at")
    list_filter = ("kind", "status")
