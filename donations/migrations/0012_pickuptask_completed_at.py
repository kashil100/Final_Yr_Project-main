from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0011_surplusfoodrequest_cooked_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pickuptask",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]