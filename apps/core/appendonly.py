"""Helpers for records that are never edited or deleted (payments, stock movements)."""

from django.db import models


class AppendOnlyQuerySet(models.QuerySet):
    def delete(self):
        raise PermissionError("These records cannot be deleted. Add a reversing entry instead.")

    def update(self, **kwargs):
        raise PermissionError("These records cannot be edited. Add a reversing entry instead.")


class AppendOnlyModel(models.Model):
    """Rows can be created but not changed or removed, in code or in the admin site."""

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise PermissionError(f"{self._meta.verbose_name.title()} records cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(f"{self._meta.verbose_name.title()} records cannot be deleted.")


class ReadOnlyAdmin:
    """Mixin for ModelAdmin: look but do not touch."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
