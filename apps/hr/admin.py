from django.contrib import admin

from apps.core.appendonly import ReadOnlyAdmin

from .models import Contract, Department, LeaveEntry, LeaveRequest, LeaveType, Licence, PublicHoliday, StaffProfile


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "head", "is_active")


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("staff_number", "last_name", "first_name", "department", "job_title")
    search_fields = ("first_name", "last_name", "staff_number")
    exclude = ("bank_account",)


@admin.register(Contract)
class ContractAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("staff", "kind", "start_date", "end_date")


@admin.register(Licence)
class LicenceAdmin(admin.ModelAdmin):
    list_display = ("staff", "body", "number", "expires_on")


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "annual_days", "uses_balance", "is_paid", "is_active")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("staff", "leave_type", "start_date", "end_date", "status")


@admin.register(LeaveEntry)
class LeaveEntryAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("staff", "leave_type", "kind", "days", "created_at")


@admin.register(PublicHoliday)
class PublicHolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name")
