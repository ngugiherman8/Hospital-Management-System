from django.db import migrations

from apps.clinical import catalogs


def load(apps, schema_editor):
    ICD10Code = apps.get_model("clinical", "ICD10Code")
    for code, title in catalogs.ICD10:
        ICD10Code.objects.get_or_create(code=code, defaults={"title": title})


class Migration(migrations.Migration):
    dependencies = [("clinical", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
