from django.db import migrations

from apps.wards import catalogs


def load(apps, schema_editor):
    Ward = apps.get_model("wards", "Ward")
    Bed = apps.get_model("wards", "Bed")
    for name, (ward_type, beds) in catalogs.WARDS.items():
        ward, _ = Ward.objects.get_or_create(name=name, defaults={"ward_type": ward_type})
        for number in beds:
            Bed.objects.get_or_create(ward=ward, number=number)


class Migration(migrations.Migration):
    dependencies = [("wards", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
