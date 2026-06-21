from django.core.management.base import BaseCommand
import time

from donations.services.notification_service import DonationNotificationService


class Command(BaseCommand):
    help = "Re-evaluate active donations, radius windows, and NGO notifications."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=0,
            help="Run continuously every N seconds. Default runs once.",
        )
        parser.add_argument(
            "--max-runs",
            type=int,
            default=0,
            help="Stop after N interval runs. Use with --interval for tests/process managers.",
        )

    def handle(self, *args, **options):
        interval = max(options["interval"], 0)
        max_runs = max(options["max_runs"], 0)
        runs = 0
        service = DonationNotificationService()

        while True:
            processed = service.reevaluate_active_donations()
            runs += 1
            self.stdout.write(
                self.style.SUCCESS(f"Re-evaluated notifications for {processed} active donation(s).")
            )

            if not interval or (max_runs and runs >= max_runs):
                break

            time.sleep(interval)
