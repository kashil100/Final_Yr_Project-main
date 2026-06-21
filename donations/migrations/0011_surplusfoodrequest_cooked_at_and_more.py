from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0010_pickuptask_delivery_otp_pickuptask_otp_verified"),
    ]

    operations = [
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="cooked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="expiry_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="safety_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="storage_type",
            field=models.CharField(
                choices=[("hot", "Hot"), ("cold", "Cold"), ("room_temp", "Room Temperature")],
                default="room_temp",
                max_length=20,
            ),
        ),
    ]
