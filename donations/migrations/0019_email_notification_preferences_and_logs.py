from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('donations', '0018_ngogalleryimage_pickup_task_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='ngoprofile',
            name='donation_notifications_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='ngoprofile',
            name='email_verified',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='donationnotificationlog',
            name='email_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='donationnotificationlog',
            name='email_provider_response',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='donationnotificationlog',
            name='email_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='donationnotificationlog',
            name='email_status',
            field=models.CharField(
                choices=[
                    ('not_sent', 'Not Sent'),
                    ('sent', 'Email Sent'),
                    ('skipped', 'Email Skipped'),
                    ('failed', 'Email Failed'),
                ],
                default='not_sent',
                max_length=20,
            ),
        ),
    ]
