from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from donations.services import send_sms


class Command(BaseCommand):
    help = "Send a test SMS using the configured SMS backend."

    def add_arguments(self, parser):
        parser.add_argument("phone_number", help="Destination phone number in E.164 format, e.g. +919876543210")
        parser.add_argument(
            "--message",
            default="HappyTummy test SMS is configured correctly.",
            help="Custom SMS body to send.",
        )

    def handle(self, *args, **options):
        phone_number = options["phone_number"]
        message = options["message"]
        backend = getattr(settings, "SMS_BACKEND", "console")

        template_data = None
        if backend == "msg91":
            template_data = {
                "restaurant_name": "HappyTummy Test Kitchen",
                "quantity": "25",
                "food_type": "meal",
                "address": "Park Street",
                "city": "Kolkata",
            }

        result = send_sms(phone_number, message, template_data=template_data)

        status = result.get("status")
        if status != "accepted":
            raise CommandError(f"SMS was not accepted by the provider: {result}")

        self.stdout.write(self.style.SUCCESS(f"SMS command finished: {result}"))
        raw_response = result.get("raw_response")
        if raw_response:
            self.stdout.write(f"Provider response: {raw_response}")
