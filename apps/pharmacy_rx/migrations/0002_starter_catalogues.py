from django.db import migrations

from apps.pharmacy_rx import catalogs


def load(apps, schema_editor):
    Drug = apps.get_model("pharmacy_rx", "Drug")
    for name, strength, form, route, keys in catalogs.DRUGS:
        Drug.objects.get_or_create(
            name=name, strength=strength, form=form, defaults={"default_route": route, "allergy_keys": keys}
        )


class Migration(migrations.Migration):
    dependencies = [("pharmacy_rx", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
