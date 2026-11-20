from django.contrib import admin

from .models import ImagingItem, ImagingModality


@admin.register(ImagingModality)
class ImagingModalityAdmin(admin.ModelAdmin):
    list_display = ("name", "dicom_code", "room", "can_send_dicom", "is_active")
    list_filter = ("is_active", "can_send_dicom")


@admin.register(ImagingItem)
class ImagingItemAdmin(admin.ModelAdmin):
    list_display = ("test", "modality", "body_part", "uses_radiation", "uses_contrast")
    list_filter = ("modality", "uses_radiation", "uses_contrast")
    search_fields = ("test__name", "body_part")
