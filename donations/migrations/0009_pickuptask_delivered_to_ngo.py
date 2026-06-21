from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0008_volunteerprofile_live_location_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="pickuptask",
            name="delivered_to_ngo",
            field=models.BooleanField(default=False),
        ),
    ]
