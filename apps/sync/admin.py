from django.contrib import admin

from apps.core.appendonly import ReadOnlyAdmin

from .models import AppliedChange, Outbox


@admin.register(Outbox)
class OutboxAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("id", "model_label", "object_id", "version", "created_at", "sent_at", "attempts")
    list_filter = ("model_label",)


@admin.register(AppliedChange)
class AppliedChangeAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("model_label", "object_id", "version", "applied", "received_at")
