from django.apps import AppConfig


class SyncConfig(AppConfig):
    name = "apps.sync"

    def ready(self):
        from . import capture

        capture.connect()
