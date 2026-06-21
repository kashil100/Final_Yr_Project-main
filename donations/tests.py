from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core import mail
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from donations.dashboard_views import CSR_CERTIFICATE_THRESHOLD
from donations.models import (
    DonationNotificationLog,
    NGOProfile,
    PickupTask,
    RestaurantProfile,
    SurplusFoodRequest,
    UserRole,
)
from donations.services import (
    _normalize_msg91_mobile,
    build_surplus_sms_variables,
    notify_nearby_ngos_about_surplus,
    send_sms,
)
from donations.services.location_service import LocationService
from donations.services.notification_service import DonationNotificationService
from donations.services.radius_service import RadiusService
from happytummy.middleware import get_server_boot_time


@override_settings(
    DONATION_NOTIFICATIONS_ASYNC=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DONATION_CLAIM_BASE_URL="https://happytummy.test",
)
class DonationNotificationFlowTests(TestCase):
    def setUp(self):
        self.restaurant_user = User.objects.create_user(username="restaurant1", email="restaurant@example.com", password="pass1234")
        self.ngo_user = User.objects.create_user(username="ngo1", email="ngo1@example.com", password="pass1234")
        self.other_ngo_user = User.objects.create_user(username="ngo2", email="ngo2@example.com", password="pass1234")

        UserRole.objects.create(user=self.restaurant_user, role="restaurant")
        UserRole.objects.create(user=self.ngo_user, role="ngo")
        UserRole.objects.create(user=self.other_ngo_user, role="ngo")

        self.restaurant = RestaurantProfile.objects.create(
            user=self.restaurant_user,
            business_name="Fresh Kitchen",
            contact_person="Riya",
            phone="9000000001",
            city="Kolkata",
            address="12 Park Street",
        )
        self.ngo = NGOProfile.objects.create(
            user=self.ngo_user,
            name="Care Shelter",
            contact_person="Aman",
            phone="9000000002",
            address="1 Mission Road",
            city="Kolkata",
            current_lat=22.5735,
            current_lng=88.3645,
        )
        self.other_ngo = NGOProfile.objects.create(
            user=self.other_ngo_user,
            name="Far Trust",
            contact_person="Sara",
            phone="9000000003",
            address="22 MG Road",
            city="Kolkata",
            current_lat=22.61,
            current_lng=88.43,
        )
        self.notification_service = DonationNotificationService()
        mail.outbox = []

    def _login_restaurant(self):
        self.client.force_login(self.restaurant_user)
        session = self.client.session
        session["server_boot"] = get_server_boot_time()
        session.save()

    def _login_ngo(self):
        self.client.force_login(self.ngo_user)
        session = self.client.session
        session["server_boot"] = get_server_boot_time()
        session.save()

    def test_radius_rules_match_business_windows(self):
        self.assertEqual(RadiusService.get_radius_for_seconds(3600).radius_km, 2)
        self.assertEqual(RadiusService.get_radius_for_seconds(7200).radius_km, 5)
        self.assertEqual(RadiusService.get_radius_for_seconds(14400).radius_km, 10)
        self.assertEqual(RadiusService.get_radius_for_seconds(21600).radius_km, 20)
        self.assertEqual(RadiusService.get_radius_for_seconds(21601).radius_km, 30)

    def test_haversine_distance_is_reasonable(self):
        distance = LocationService.haversine_distance_km(22.5726, 88.3639, 22.5735, 88.3645)
        self.assertGreater(distance, 0)
        self.assertLess(distance, 1)

    @override_settings(SMS_BACKEND="console")
    @patch("donations.services.location_service.LocationService.sync_restaurant_coordinates", return_value=(22.5726, 88.3639))
    def test_new_donation_creates_single_notification_per_ngo(self, _mock_geo):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Rice",
            quantity=30,
            expiry_at=timezone.now() + timedelta(hours=2),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )

        results_first = self.notification_service.process_new_donation(donation)
        results_second = self.notification_service.sync_notifications_for_donation(donation, trigger="periodic")

        self.assertEqual(DonationNotificationLog.objects.filter(donation=donation, ngo=self.ngo).count(), 1)
        self.assertTrue(any(result["ngo_id"] == self.ngo.id for result in results_first))
        self.assertTrue(any(result["status"] == "existing" for result in results_second))

    @override_settings(SMS_BACKEND="console")
    def test_new_donation_emails_each_eligible_ngo_once(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Fruit Bowls",
            quantity=16,
            expiry_at=timezone.now() + timedelta(hours=2),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )

        self.notification_service.process_new_donation(donation)
        self.notification_service.sync_notifications_for_donation(donation, trigger="periodic")

        notification = DonationNotificationLog.objects.get(donation=donation, ngo=self.ngo)
        self.assertEqual(notification.email_status, "sent")
        self.assertIsNotNone(notification.email_sent_at)
        self.assertEqual(len([email for email in mail.outbox if self.ngo_user.email in email.to]), 1)
        self.assertIn("Fruit Bowls", mail.outbox[0].body)
        self.assertIn("https://happytummy.test/dashboard/ngo/", mail.outbox[0].body)

    @override_settings(SMS_BACKEND="console")
    def test_ineligible_ngos_do_not_receive_donation_email(self):
        self.ngo.email_verified = False
        self.ngo.save(update_fields=["email_verified"])
        self.other_ngo.donation_notifications_enabled = False
        self.other_ngo.save(update_fields=["donation_notifications_enabled"])
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Packed Meals",
            quantity=24,
            expiry_at=timezone.now() + timedelta(hours=7),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )

        results = self.notification_service.process_new_donation(donation)

        self.assertEqual(results, [])
        self.assertEqual(DonationNotificationLog.objects.filter(donation=donation).count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(DONATION_NOTIFICATIONS_ASYNC=True)
    @patch("donations.services.notification_service._notification_executor.submit")
    def test_public_notification_trigger_queues_background_job(self, mock_submit):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Noodles",
            quantity=10,
            expiry_at=timezone.now() + timedelta(hours=2),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )

        with self.captureOnCommitCallbacks(execute=True):
            results = notify_nearby_ngos_about_surplus(donation)

        self.assertEqual(results, [{"donation_id": donation.id, "status": "queued"}])
        mock_submit.assert_called_once()

    @override_settings(SMS_BACKEND="console")
    def test_radius_shrink_withdraws_farther_ngo(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Curry",
            quantity=20,
            expiry_at=timezone.now() + timedelta(hours=7),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )

        self.notification_service.process_new_donation(donation)
        self.assertTrue(DonationNotificationLog.objects.filter(donation=donation, ngo=self.other_ngo).exists())

        donation.expiry_at = timezone.now() + timedelta(minutes=50)
        donation.save(update_fields=["expiry_at"])
        self.notification_service.sync_notifications_for_donation(donation, trigger="periodic")

        other_notification = DonationNotificationLog.objects.get(donation=donation, ngo=self.other_ngo)
        self.assertEqual(other_notification.status, "withdrawn")
        self.assertFalse(other_notification.is_active)

    @override_settings(SMS_BACKEND="console")
    def test_expired_donation_is_archived_and_notifications_stop(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Bread",
            quantity=10,
            expiry_at=timezone.now() - timedelta(minutes=5),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )
        DonationNotificationLog.objects.create(
            donation=donation,
            ngo=self.ngo,
            status="sent",
            radius_km=5,
            is_active=True,
        )

        self.notification_service.reevaluate_active_donations()

        donation.refresh_from_db()
        notification = DonationNotificationLog.objects.get(donation=donation, ngo=self.ngo)
        self.assertEqual(donation.donation_status, "archived")
        self.assertEqual(notification.status, "expired")
        self.assertFalse(notification.is_active)

    @override_settings(SMS_BACKEND="console")
    def test_accepted_donation_is_not_reevaluated_or_archived(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Accepted Meals",
            quantity=12,
            expiry_at=timezone.now() - timedelta(minutes=5),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
            is_picked=True,
            accepted_by_ngo=self.ngo,
            donation_status="accepted",
        )
        DonationNotificationLog.objects.create(
            donation=donation,
            ngo=self.ngo,
            status="accepted",
            radius_km=5,
            is_active=False,
        )

        processed = self.notification_service.reevaluate_active_donations()

        donation.refresh_from_db()
        self.assertEqual(processed, 0)
        self.assertEqual(donation.donation_status, "accepted")
        self.assertEqual(donation.accepted_by_ngo, self.ngo)

    @override_settings(SMS_BACKEND="console")
    def test_ngo_can_accept_only_if_notification_exists(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Meals",
            quantity=40,
            expiry_at=timezone.now() + timedelta(hours=3),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )
        self.notification_service.process_new_donation(donation)

        accepted = self.notification_service.accept_donation(donation, self.ngo)

        donation.refresh_from_db()
        self.assertTrue(accepted)
        self.assertTrue(donation.is_picked)
        self.assertEqual(donation.accepted_by_ngo, self.ngo)
        self.assertEqual(donation.donation_status, "accepted")

    def test_dashboard_notifications_filter_stale_out_of_radius_rows(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Too Far",
            quantity=14,
            expiry_at=timezone.now() + timedelta(hours=1),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
            current_radius_km=2,
            donation_status="notifying",
        )
        DonationNotificationLog.objects.create(
            donation=donation,
            ngo=self.ngo,
            status="sent",
            radius_km=30,
            distance_km=5,
            is_active=True,
        )

        notifications = self.notification_service.get_dashboard_notifications(self.ngo)

        self.assertEqual(notifications, [])

    @override_settings(SMS_BACKEND="console")
    def test_reevaluation_command_supports_bounded_interval_runs(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Command Meals",
            quantity=9,
            expiry_at=timezone.now() + timedelta(hours=3),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )

        call_command("reevaluate_donation_notifications", interval=1, max_runs=1)

        donation.refresh_from_db()
        self.assertEqual(donation.donation_status, "notifying")
        self.assertEqual(donation.current_radius_km, 10)

    @override_settings(SMS_BACKEND="console")
    @patch("donations.dashboard_views.notify_nearby_ngos_about_surplus")
    @patch("donations.services.location_service.LocationService.sync_restaurant_coordinates", return_value=(22.5726, 88.3639))
    def test_restaurant_dashboard_add_donation_triggers_notification_flow(self, _mock_geo, mock_notify):
        self._login_restaurant()
        mock_notify.return_value = [{"ngo_id": self.ngo.id, "phone": self.ngo.phone, "status": "skipped", "reason": "console-backend"}]

        response = self.client.post(
            reverse("restaurant_dashboard"),
            data={
                "action": "add_donation",
                "food_type": "Veg Biryani",
                "quantity": "40",
                "cooked_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "expiry_at": (timezone.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "storage_type": "hot",
                "safety_notes": "Packed and sealed",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        donation = SurplusFoodRequest.objects.get(restaurant=self.restaurant)
        mock_notify.assert_called_once_with(donation)

    @override_settings(SMS_BACKEND="console")
    @patch("donations.dashboard_views.notify_nearby_ngos_about_surplus")
    @patch("donations.services.location_service.LocationService.sync_restaurant_coordinates", return_value=(22.5726, 88.3639))
    def test_restaurant_dashboard_shows_demo_sms_phone_numbers_in_console_mode(self, _mock_geo, mock_notify):
        self._login_restaurant()
        mock_notify.return_value = [{"ngo_id": self.ngo.id, "phone": self.ngo.phone, "status": "skipped", "reason": "console-backend"}]

        response = self.client.post(
            reverse("restaurant_dashboard"),
            data={
                "action": "add_donation",
                "food_type": "Veg Biryani",
                "quantity": "40",
                "cooked_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "expiry_at": (timezone.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "storage_type": "hot",
                "safety_notes": "Packed and sealed",
            },
            follow=True,
        )

        message_texts = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("9000000002" in message for message in message_texts))
        self.assertTrue(any("demo SMS mode" in message for message in message_texts))

    @override_settings(SMS_BACKEND="console")
    def test_ngo_notification_api_returns_live_payload(self):
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Pasta",
            quantity=18,
            expiry_at=timezone.now() + timedelta(hours=2),
            restaurant_lat=22.5726,
            restaurant_lng=88.3639,
        )
        self.notification_service.process_new_donation(donation)
        self._login_ngo()

        response = self.client.get(reverse("ngo_notification_feed"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["notifications"]), 1)
        self.assertEqual(payload["notifications"][0]["food_type"], "Pasta")

    def test_build_surplus_sms_variables_matches_expected_fields(self):
        surplus = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Rice",
            quantity=30,
        )

        self.assertEqual(
            build_surplus_sms_variables(surplus),
            {
                "restaurant_name": "Fresh Kitchen",
                "quantity": "30",
                "food_type": "Rice",
                "address": "12 Park Street",
                "city": "Kolkata",
            },
        )

    def test_normalize_msg91_mobile_converts_indian_numbers(self):
        self.assertEqual(_normalize_msg91_mobile("9000000002"), "919000000002")
        self.assertEqual(_normalize_msg91_mobile("+91 90000 00002"), "919000000002")

    @override_settings(
        SMS_BACKEND="msg91",
        MSG91_AUTH_KEY="abc123xyz789exampleauthkey",
        MSG91_FLOW_ID="67f8a1b2c3d4e5f678901234",
        MSG91_SENDER_ID="HAPPTY",
    )
    def test_send_sms_skips_placeholder_msg91_configuration(self):
        result = send_sms(
            "+919000000002",
            "Test message",
            template_data={"restaurant_name": "Fresh Kitchen"},
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "placeholder-msg91-config")

    def test_restaurant_dashboard_shows_csr_certificate_progress(self):
        self._login_restaurant()
        donation = SurplusFoodRequest.objects.create(
            restaurant=self.restaurant,
            food_type="Rice",
            quantity=15,
        )
        PickupTask.objects.create(request=donation, completed=True, completed_at=timezone.now())

        response = self.client.get(reverse("restaurant_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["csr_certificate_eligible"])
        self.assertEqual(
            response.context["donations_remaining_for_certificate"],
            CSR_CERTIFICATE_THRESHOLD - 1,
        )
