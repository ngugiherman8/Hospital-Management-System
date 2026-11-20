from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import FailedLogin, TOTPDevice, User


@admin.register(User)
class HospitalUserAdmin(UserAdmin):
    list_display = ("username", "get_full_name", "role", "department", "is_active")
    list_filter = ("role", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Hospital", {"fields": ("role", "department", "phone")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Hospital", {"fields": ("role", "department", "phone")}),)


@admin.register(TOTPDevice)
class TOTPDeviceAdmin(admin.ModelAdmin):
    """Deleting a device lets an administrator who lost their phone set up again."""

    list_display = ("user", "confirmed_at")
    exclude = ("secret", "last_step")
    readonly_fields = ("user", "confirmed_at")

    def has_add_permission(self, request):
        return False


@admin.register(FailedLogin)
class FailedLoginAdmin(admin.ModelAdmin):
    list_display = ("username", "ip_address", "at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
