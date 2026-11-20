"""Cloud copy only: keep ids made on the cloud apart from ids copied from the hospital.

The hospital server's ids are copied as they are. If the cloud also handed out small ids (an audit entry for a
sign-in, say), a later copied row could land on the same id. So every id counter on the cloud starts at one billion.
Safe to run again.
"""

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.sync import capture

FLOOR = 1_000_000_000


class Command(BaseCommand):
    help = "Move the cloud copy's id counters out of the range the hospital server uses."

    def handle(self, *args, **options):
        if settings.HMS_MODE != "cloud":
            raise CommandError("This only makes sense on the cloud copy (HMS_MODE=cloud).")
        moved = 0
        with connection.cursor() as cursor:
            for model in apps.get_models():
                pk = model._meta.pk
                if not issubclass(model, capture.TRACKED_BASES) or pk.get_internal_type() not in (
                    "AutoField",
                    "BigAutoField",
                    "SmallAutoField",
                ):
                    continue
                cursor.execute("SELECT pg_get_serial_sequence(%s, %s)", [model._meta.db_table, pk.column])
                sequence = cursor.fetchone()[0]
                if sequence:
                    cursor.execute(
                        "SELECT setval(%s, GREATEST((SELECT last_value FROM " + sequence + "), %s))", [sequence, FLOOR]
                    )
                    moved += 1
        self.stdout.write(f"{moved} id counters now start at {FLOOR:,}.")
