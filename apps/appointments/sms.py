import logging
from importlib import import_module

from django.conf import settings

log = logging.getLogger(__name__)


class ConsoleSMSBackend:
    """Development backend: logs the message instead of sending it."""

    def send(self, to: str, message: str) -> bool:
        log.info("SMS to %s: %s", to, message)
        return True


def send_sms(to: str, message: str) -> bool:
    module, _, cls = settings.SMS_BACKEND.rpartition(".")
    return getattr(import_module(module), cls)().send(to, message)
