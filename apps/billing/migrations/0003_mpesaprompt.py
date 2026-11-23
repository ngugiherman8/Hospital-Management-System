import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_claim_copay_and_followup"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MpesaPrompt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("phone", models.CharField(max_length=15)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Waiting for the patient"),
                            ("success", "Paid"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled by the patient"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("checkout_request_id", models.CharField(blank=True, db_index=True, max_length=64)),
                ("merchant_request_id", models.CharField(blank=True, max_length=64)),
                ("result_desc", models.CharField(blank=True, max_length=200)),
                ("mpesa_code", models.CharField(blank=True, max_length=20)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "invoice",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mpesa_prompts",
                        to="billing.invoice",
                    ),
                ),
            ],
            options={
                "ordering": ["-id"],
            },
        ),
    ]
