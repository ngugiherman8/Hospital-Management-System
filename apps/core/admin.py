from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AuditLog, Department, LoginEvent, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "department", "is_active")
    list_filter = ("role", "is_active", "department")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Hospital", {"fields": ("role", "phone", "department", "two_factor_required")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Hospital", {"fields": ("role", "phone", "department")}),
    )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("at", "user", "action", "object_repr", "needs_review")
    list_filter = ("action", "needs_review")
    search_fields = ("object_repr", "user__username")


@admin.register(LoginEvent)
class LoginEventAdmin(ReadOnlyAdmin):
    list_display = ("at", "username", "kind", "ip_address")
    list_filter = ("kind",)
