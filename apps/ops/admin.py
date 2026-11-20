from django.contrib import admin

from apps.core.appendonly import ReadOnlyAdmin

from .models import CheckState


@admin.register(CheckState)
class CheckStateAdmin(ReadOnlyAdmin, admin.ModelAdmin):
    list_display = ("name", "failing_since", "last_alerted_at", "message")
