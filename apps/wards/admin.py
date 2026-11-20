from django.contrib import admin

from .models import Bed, Ward


class BedInline(admin.TabularInline):
    model = Bed
    extra = 1
    fields = ("number", "status")


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ("name", "kind")
    inlines = [BedInline]
