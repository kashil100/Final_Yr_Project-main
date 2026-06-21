from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0009_pickuptask_delivered_to_ngo"),
    ]

    operations = [
        migrations.AddField(
            model_name="pickuptask",
            name="delivery_otp",
            field=models.CharField(blank=True, max_length=6),
        ),
        migrations.AddField(
            model_name="pickuptask",
            name="otp_verified",
            field=models.BooleanField(default=False),
        ),
    ]
