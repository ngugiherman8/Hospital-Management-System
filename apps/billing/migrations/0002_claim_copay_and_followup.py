from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="claim",
            name="patient_portion",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="The patient's co-pay. It is not part of the amount claimed from the payer.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="claim",
            name="last_followed_up_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
