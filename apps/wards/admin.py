from django.contrib import admin

from .models import Admission, Bed, Ward


class BedInline(admin.TabularInline):
    model = Bed
    extra = 1


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ("name", "ward_type", "is_active")
    inlines = [BedInline]


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = ("number", "patient", "bed", "status", "admitted_at", "discharged_at")
    list_filter = ("status",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
