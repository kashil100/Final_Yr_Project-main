from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0012_pickuptask_completed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="ngoprofile",
            name="current_lat",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ngoprofile",
            name="current_lng",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ngoprofile",
            name="priority_score",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="accepted_by_ngo",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="accepted_surplus_donations",
                to="donations.ngoprofile",
            ),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="current_radius_km",
            field=models.IntegerField(default=5),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="donation_status",
            field=models.CharField(
                choices=[
                    ("posted", "Posted"),
                    ("notifying", "Notifying NGOs"),
                    ("accepted", "Accepted by NGO"),
                    ("picked", "Picked Up"),
                    ("expired", "Expired"),
                    ("archived", "Archived"),
                ],
                default="posted",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="expiry_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("manual_delete", "Manually Deleted by Restaurant"),
                    ("auto_expired", "Auto-Expired - No Acceptance"),
                    ("picked_up", "Successfully Picked Up"),
                ],
                max_length=50,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="last_radius_expansion_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="ngos_notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="notification_debug",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="notified_ngo_ids",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="posted_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="restaurant_lat",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="surplusfoodrequest",
            name="restaurant_lng",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="surplusfoodrequest",
            index=models.Index(fields=["donation_status", "expiry_at"], name="donations_s_donatio_b64344_idx"),
        ),
        migrations.AddIndex(
            model_name="surplusfoodrequest",
            index=models.Index(fields=["restaurant", "donation_status"], name="donations_s_restaur_8dcc58_idx"),
        ),
        migrations.CreateModel(
            name="DonationNotificationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("sent", "SMS Sent"), ("pending", "Awaiting Response"), ("read", "Read on Dashboard"), ("accepted", "Accepted"), ("rejected", "Not Interested"), ("withdrawn", "Withdrawn - Out Of Radius"), ("expired", "Donation Expired"), ("failed", "Send Failed")], default="pending", max_length=20)),
                ("radius_km", models.IntegerField()),
                ("distance_km", models.FloatField(blank=True, null=True)),
                ("notified_at", models.DateTimeField(auto_now_add=True)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("response_time_seconds", models.IntegerField(blank=True, null=True)),
                ("is_read", models.BooleanField(default=False)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sms_provider_response", models.JSONField(default=dict)),
                ("debug_context", models.JSONField(blank=True, default=dict)),
                ("donation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notification_logs", to="donations.surplusfoodrequest")),
                ("ngo", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to="donations.ngoprofile")),
            ],
            options={
                "ordering": ["-notified_at"],
                "indexes": [
                    models.Index(fields=["donation", "status"], name="donations_d_donatio_67820a_idx"),
                    models.Index(fields=["ngo", "status"], name="donations_d_ngo_id_531e51_idx"),
                    models.Index(fields=["ngo", "is_active", "is_read"], name="donations_d_ngo_id_9af8c2_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("donation", "ngo"), name="unique_donation_notification_per_ngo"),
                ],
            },
        ),
    ]
