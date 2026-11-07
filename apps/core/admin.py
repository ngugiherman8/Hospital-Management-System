from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, Department, LoginEvent, User


@admin.register(User)
class HMSUserAdmin(UserAdmin):
    list_display = ("username", "get_full_name", "role", "department", "is_active")
    list_filter = ("role", "department", "is_active")
    fieldsets = UserAdmin.fieldsets + (
        ("Hospital", {"fields": ("role", "department", "phone", "two_factor_required")}),
    )


admin.site.register(Department)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "action", "object_repr", "needs_review")
    list_filter = ("action", "needs_review")
    search_fields = ("object_repr", "user__username")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name not in ("needs_review", "reviewed_by")]


@admin.register(LoginEvent)
class LoginEventAdmin(admin.ModelAdmin):
    list_display = ("at", "username", "kind", "ip_address")
    list_filter = ("kind",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
