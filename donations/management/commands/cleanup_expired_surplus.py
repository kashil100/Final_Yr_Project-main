from django.core.management.base import BaseCommand
from donations.services.notification_service import DonationNotificationService

class Command(BaseCommand):
    help = 'Archives expired surplus food requests and withdraws their NGO notifications.'

    def handle(self, *args, **options):
        processed = DonationNotificationService().reevaluate_active_donations()
        self.stdout.write(self.style.SUCCESS(f'Processed {processed} donation(s) for expiry cleanup.'))
