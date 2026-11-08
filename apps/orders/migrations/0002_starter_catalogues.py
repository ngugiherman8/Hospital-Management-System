from django.db import migrations

from apps.orders import catalogs


def load(apps, schema_editor):
    TestCatalogue = apps.get_model("orders", "TestCatalogue")
    for kind, name, sample, normal in catalogs.TESTS:
        TestCatalogue.objects.get_or_create(
            name=name, defaults={"kind": kind, "sample_type": sample, "normal_range": normal}
        )


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
