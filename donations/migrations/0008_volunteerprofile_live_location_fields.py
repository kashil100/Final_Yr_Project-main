from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0007_pickuptask_ngo_request_alter_pickuptask_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="volunteerprofile",
            name="current_lat",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="volunteerprofile",
            name="current_lng",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="volunteerprofile",
            name="location_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
