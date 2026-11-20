from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = "apps.billing"

    def ready(self):
        from . import signals  # noqa: F401
