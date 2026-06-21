from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ("donations", "0003_alter_restaurantprofile_city_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="NGOFoodRequest",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("food_type", models.CharField(max_length=120)),
                ("quantity", models.PositiveIntegerField()),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("fulfilled", models.BooleanField(default=False)),
                ("ngo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="donations.ngoprofile")),
            ],
        ),
    ]