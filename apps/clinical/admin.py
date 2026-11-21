from django.contrib import admin

from . import models as m


class ReadOnlyAdmin(admin.ModelAdmin):
    """Clinical records are read-only here: they change only through the clinical screens (and are audited)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(m.Consultation)
class ConsultationAdmin(ReadOnlyAdmin):
    list_display = ("appointment", "outcome")
