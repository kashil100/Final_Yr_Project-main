import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import F, Q
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from donations.models import DonationNotificationLog, NGOProfile, SurplusFoodRequest
from donations.services.location_service import LocationService
from donations.services.radius_service import RadiusService


logger = logging.getLogger(__name__)
_notification_executor = ThreadPoolExecutor(
    max_workers=getattr(settings, "DONATION_NOTIFICATION_WORKERS", 2)
)


def _normalize_city(city):
    return (city or "").strip().lower()


def _normalize_msg91_mobile(phone_number):
    digits = "".join(ch for ch in str(phone_number or "") if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        return digits
    if len(digits) == 10:
        return f"91{digits}"
    return digits


def _looks_like_placeholder(value):
    normalized = (value or "").strip().lower()
    if not normalized:
        return True

    placeholder_markers = (
        "your_",
        "example",
        "abc123",
        "changeme",
        "replace",
    )
    return any(marker in normalized for marker in placeholder_markers)


def get_nearby_ngos_for_surplus(surplus_request):
    radius_km = RadiusService.get_radius_for_donation(surplus_request).radius_km
    ngos, _ = LocationService.get_nearby_ngos(
        surplus_request.restaurant_lat,
        surplus_request.restaurant_lng,
        radius_km,
        city=getattr(surplus_request.restaurant, "city", ""),
    )
    if ngos:
        return [
            ngo for ngo in ngos
            if ngo.user.is_active
            and getattr(ngo, "email_verified", True)
            and getattr(ngo, "donation_notifications_enabled", True)
        ]

    city = _normalize_city(getattr(surplus_request.restaurant, "city", ""))
    if not city:
        return NGOProfile.objects.none()

    return NGOProfile.objects.filter(
        city__iexact=surplus_request.restaurant.city,
        user__is_active=True,
        email_verified=True,
        donation_notifications_enabled=True,
    ).exclude(phone__isnull=True).exclude(phone__exact="")


def build_surplus_sms_message(surplus_request):
    restaurant = surplus_request.restaurant
    food_type = surplus_request.food_type
    quantity = surplus_request.quantity
    address = restaurant.address
    city = restaurant.city
    return (
        f"HappyTummy alert: {restaurant.business_name} has posted {quantity} "
        f"surplus {food_type} meals near {address}, {city}. "
        f"Log in to claim this donation."
    )


def build_surplus_sms_variables(surplus_request):
    restaurant = surplus_request.restaurant
    return {
        "restaurant_name": restaurant.business_name,
        "quantity": str(surplus_request.quantity),
        "food_type": surplus_request.food_type,
        "address": restaurant.address,
        "city": restaurant.city,
    }


def build_donation_claim_url():
    base_url = getattr(settings, "DONATION_CLAIM_BASE_URL", "http://127.0.0.1:8000")
    return urljoin(f"{base_url.rstrip('/')}/", reverse("ngo_dashboard").lstrip("/"))


def build_surplus_email_context(surplus_request, ngo):
    restaurant = surplus_request.restaurant
    pickup_location = ", ".join(
        part
        for part in [
            restaurant.address,
            restaurant.city,
            getattr(restaurant, "district", None),
            getattr(restaurant, "state", None),
        ]
        if part
    )
    return {
        "ngo": ngo,
        "donation": surplus_request,
        "restaurant_name": restaurant.business_name,
        "food_type": surplus_request.food_type,
        "quantity": surplus_request.quantity,
        "pickup_location": pickup_location,
        "expiry_at": surplus_request.expiry_at,
        "pickup_deadline": surplus_request.expiry_at,
        "claim_url": build_donation_claim_url(),
    }


def is_ngo_eligible_for_donation_email(ngo):
    user = getattr(ngo, "user", None)
    return bool(
        user
        and user.is_active
        and user.email
        and getattr(ngo, "email_verified", False)
        and getattr(ngo, "donation_notifications_enabled", True)
    )


def send_surplus_donation_email(surplus_request, ngo):
    if not is_ngo_eligible_for_donation_email(ngo):
        return {"status": "skipped", "reason": "ngo-not-eligible-for-email"}

    context = build_surplus_email_context(surplus_request, ngo)
    subject = f"New HappyTummy donation: {surplus_request.food_type}"
    text_body = render_to_string("emails/donation_notification.txt", context)
    html_body = render_to_string("emails/donation_notification.html", context)
    from_email = getattr(settings, "DONATION_NOTIFICATION_SENDER_EMAIL", None) or settings.DEFAULT_FROM_EMAIL
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=[ngo.user.email],
    )
    message.attach_alternative(html_body, "text/html")
    sent_count = message.send(fail_silently=True)
    # try:
    #     sent_count = message.send(fail_silently=True)
    # except Exception as e:
    #     logger.exception("Email sending failed: %s", e)
    #     return {
    #         "status": "failed",
    #         "reason": str(e),
    #     }
    return {
        "status": "sent" if sent_count else "failed",
        "recipient": ngo.user.email,
        "sent_count": sent_count,
    }


def _send_twilio_sms(phone_number, message):
    account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    from_number = getattr(settings, "TWILIO_FROM_NUMBER", "")

    if not account_sid or not auth_token or not from_number:
        logger.warning("Twilio SMS is not fully configured; skipping SMS send.")
        return {"status": "skipped", "reason": "missing-twilio-config"}
    if any(_looks_like_placeholder(value) for value in (account_sid, auth_token, from_number)):
        logger.warning("Twilio SMS contains placeholder credentials; skipping SMS send.")
        return {"status": "skipped", "reason": "placeholder-twilio-config"}

    response = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{quote(account_sid, safe='')}/Messages.json",
        auth=(account_sid, auth_token),
        data={
            "From": from_number,
            "To": phone_number,
            "Body": message,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "status": "accepted",
        "sid": payload.get("sid"),
        "provider": "twilio",
    }


def _send_msg91_sms(phone_number, template_data):
    auth_key = getattr(settings, "MSG91_AUTH_KEY", "")
    flow_id = getattr(settings, "MSG91_FLOW_ID", "")
    sender_id = getattr(settings, "MSG91_SENDER_ID", "")

    if not auth_key or not flow_id or not sender_id:
        logger.warning("MSG91 SMS is not fully configured; skipping SMS send.")
        return {"status": "skipped", "reason": "missing-msg91-config"}
    if any(_looks_like_placeholder(value) for value in (auth_key, flow_id, sender_id)):
        logger.warning("MSG91 SMS contains placeholder credentials; skipping SMS send.")
        return {"status": "skipped", "reason": "placeholder-msg91-config"}

    mobile = _normalize_msg91_mobile(phone_number)
    if not mobile:
        return {"status": "failed", "reason": "invalid-phone-number"}

    payload = {
        "flow_id": flow_id,
        "sender": sender_id,
        "recipients": [
            {
                "mobiles": mobile,
                **(template_data or {}),
            }
        ],
    }

    response = requests.post(
        "https://api.msg91.com/api/v5/flow/",
        headers={
            "authkey": auth_key,
            "content-type": "application/json",
        },
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("type") == "error":
        return {
            "status": "failed",
            "reason": body.get("message") or "msg91-error",
            "provider": "msg91",
            "raw_response": body,
        }
    return {
        "status": "accepted",
        "sid": body.get("message"),
        "provider": "msg91",
        "raw_response": body,
    }


def send_sms(phone_number, message, template_data=None):
    backend = getattr(settings, "SMS_BACKEND", "console")

    if backend == "console":
        logger.info("SMS to %s: %s", phone_number, message)
        return {"status": "skipped", "reason": "console-backend"}

    if backend == "twilio":
        return _send_twilio_sms(phone_number, message)

    if backend == "msg91":
        return _send_msg91_sms(phone_number, template_data)

    logger.warning("Unsupported SMS backend configured: %s", backend)
    return {"status": "skipped", "reason": "unsupported-backend"}


class DonationNotificationService:
    ACTIVE_NOTIFICATION_STATUSES = {"pending", "sent", "read"}
    FINAL_DONATION_STATUSES = {"accepted", "picked", "archived", "expired"}

    def process_new_donation(self, donation):
        self._ensure_restaurant_coordinates(donation)
        return self.sync_notifications_for_donation(donation, trigger="created")

    def reevaluate_active_donations(self):
        now = timezone.now()
        active_donations = SurplusFoodRequest.objects.filter(
            donation_status__in=["posted", "notifying"],
            expiry_at__isnull=False,
            is_picked=False,
            accepted_by_ngo__isnull=True,
        ).select_related("restaurant", "accepted_by_ngo")

        processed = 0
        for donation in active_donations:
            if donation.expiry_at and donation.expiry_at <= now:
                self.expire_donation_notifications(donation)
                processed += 1
                continue
            self.sync_notifications_for_donation(donation, trigger="periodic")
            processed += 1
        return processed

    def expire_donation_notifications(self, donation):
        if not donation.is_radius_reevaluation_active:
            return
        donation.mark_as_expired(reason="auto_expired")
        DonationNotificationLog.objects.filter(
            donation=donation,
            status__in=list(self.ACTIVE_NOTIFICATION_STATUSES),
        ).update(
            status="expired",
            is_active=False,
            responded_at=timezone.now(),
        )

    def sync_notifications_for_donation(self, donation, *, trigger):
        if not donation.is_radius_reevaluation_active:
            return []

        if donation.time_remaining_seconds is not None and donation.time_remaining_seconds <= 0:
            self.expire_donation_notifications(donation)
            return []

        self._ensure_restaurant_coordinates(donation)
        decision = RadiusService.get_radius_for_donation(donation)
        eligible_ngos = self._get_eligible_ngos(donation, decision.radius_km)
        eligible_ids = {ngo.id for ngo in eligible_ngos}
        now = timezone.now()

        with transaction.atomic():
            donation.current_radius_km = decision.radius_km
            donation.ngos_notified_at = donation.ngos_notified_at or now
            donation.last_radius_expansion_at = now
            donation.donation_status = "notifying"
            donation.notified_ngo_ids = sorted(eligible_ids)
            donation.notification_debug = {
                "trigger": trigger,
                "radius_rule": decision.debug_label,
                "seconds_remaining": decision.seconds_remaining,
                "eligible_ngo_count": len(eligible_ids),
                "evaluated_at": now.isoformat(),
            }
            donation.save(
                update_fields=[
                    "current_radius_km",
                    "ngos_notified_at",
                    "last_radius_expansion_at",
                    "donation_status",
                    "notified_ngo_ids",
                    "notification_debug",
                ]
            )

            withdrawn_count = self._withdraw_out_of_radius_notifications(donation, eligible_ids)
            if withdrawn_count:
                donation.notification_debug = {
                    **donation.notification_debug,
                    "withdrawn_count": withdrawn_count,
                }
                donation.save(update_fields=["notification_debug"])

            results = []
            message = build_surplus_sms_message(donation)
            template_data = build_surplus_sms_variables(donation)

            for ngo in eligible_ngos:
                result = self._upsert_notification_and_send(
                    donation=donation,
                    ngo=ngo,
                    radius_km=decision.radius_km,
                    distance_km=getattr(ngo, "distance_km", None),
                    message=message,
                    template_data=template_data,
                    trigger=trigger,
                )
                results.append(result)

            return results

    def get_dashboard_notifications(self, ngo_profile):
        queryset = DonationNotificationLog.objects.filter(
            ngo=ngo_profile,
            is_active=True,
            status__in=list(self.ACTIVE_NOTIFICATION_STATUSES),
            donation__donation_status__in=["posted", "notifying"],
            donation__is_picked=False,
            donation__accepted_by_ngo__isnull=True,
        ).select_related("donation", "donation__restaurant").order_by(
            "-notified_at",
            "distance_km",
        )
        now = timezone.now()
        queryset = queryset.filter(
            Q(donation__expiry_at__isnull=True) | Q(donation__expiry_at__gt=now),
        ).filter(
            Q(distance_km__isnull=True) | Q(distance_km__lte=F("donation__current_radius_km")),
        )
        notifications = []
        for notification in queryset:
            donation = notification.donation
            donation.notification_id = notification.id
            donation.notification_sent_at = notification.notified_at
            donation.notification_distance_km = notification.distance_km
            donation.notification_radius_km = notification.radius_km
            donation.notification_is_read = notification.is_read
            donation.notification_status = notification.status
            notifications.append(donation)
        return notifications

    def get_dashboard_payload(self, ngo_profile):
        notifications = self.get_dashboard_notifications(ngo_profile)
        payload = []
        for donation in notifications:
            payload.append(
                {
                    "id": donation.id,
                    "notification_id": donation.notification_id,
                    "food_type": donation.food_type,
                    "quantity": donation.quantity,
                    "restaurant_name": donation.restaurant.business_name,
                    "posted_at": donation.notification_sent_at.isoformat() if donation.notification_sent_at else donation.timestamp.isoformat(),
                    "expires_at": donation.expiry_at.isoformat() if donation.expiry_at else None,
                    "time_remaining_readable": donation.time_remaining_readable,
                    "time_remaining_seconds": donation.time_remaining_seconds,
                    "safety_status": donation.safety_status,
                    "distance_km": round(donation.notification_distance_km, 2) if donation.notification_distance_km is not None else None,
                    "radius_km": donation.notification_radius_km,
                    "is_read": donation.notification_is_read,
                    "status": donation.notification_status,
                }
            )
        unread_count = sum(1 for item in payload if not item["is_read"])
        return {"notifications": payload, "unread_count": unread_count}

    def mark_notifications_as_read(self, ngo_profile):
        DonationNotificationLog.objects.filter(
            ngo=ngo_profile,
            is_active=True,
            is_read=False,
            status__in=["pending", "sent"],
        ).update(
            is_read=True,
            read_at=timezone.now(),
            status="read",
        )

    def accept_donation(self, donation, ngo_profile):
        now = timezone.now()
        with transaction.atomic():
            locked_donation = SurplusFoodRequest.objects.select_for_update().get(id=donation.id)
            if not locked_donation.can_be_accepted_now:
                return False

            notification = DonationNotificationLog.objects.select_for_update().filter(
                donation=locked_donation,
                ngo=ngo_profile,
                is_active=True,
                status__in=list(self.ACTIVE_NOTIFICATION_STATUSES),
            ).filter(
                Q(distance_km__isnull=True) | Q(distance_km__lte=F("donation__current_radius_km")),
            ).first()
            if not notification:
                return False

            locked_donation.is_picked = True
            locked_donation.accepted_by_ngo = ngo_profile
            locked_donation.donation_status = "accepted"
            locked_donation.save(update_fields=["is_picked", "accepted_by_ngo", "donation_status"])

            donation.is_picked = locked_donation.is_picked
            donation.accepted_by_ngo = locked_donation.accepted_by_ngo
            donation.donation_status = locked_donation.donation_status

            notification.status = "accepted"
            notification.is_active = False
            notification.is_read = True
            notification.read_at = notification.read_at or now
            notification.responded_at = now
            if notification.notified_at:
                notification.response_time_seconds = int((now - notification.notified_at).total_seconds())
            notification.save(
                update_fields=[
                    "status",
                    "is_active",
                    "is_read",
                    "read_at",
                    "responded_at",
                    "response_time_seconds",
                ]
            )

            DonationNotificationLog.objects.filter(
                donation=locked_donation,
            ).exclude(id=notification.id).filter(
                status__in=list(self.ACTIVE_NOTIFICATION_STATUSES),
            ).update(
                status="withdrawn",
                is_active=False,
                responded_at=now,
            )
        return True

    def _ensure_restaurant_coordinates(self, donation):
        if donation.restaurant_lat is not None and donation.restaurant_lng is not None:
            return
        lat, lng = LocationService.sync_restaurant_coordinates(donation.restaurant)
        if lat is None or lng is None:
            return
        donation.restaurant_lat = lat
        donation.restaurant_lng = lng
        donation.save(update_fields=["restaurant_lat", "restaurant_lng"])

    def _get_eligible_ngos(self, donation, radius_km):
        ngos, used_db_distance = LocationService.get_nearby_ngos(
            donation.restaurant_lat,
            donation.restaurant_lng,
            radius_km,
            city=donation.restaurant.city,
        )
        ngos = [
            ngo for ngo in ngos
            if ngo.user.is_active
            and getattr(ngo, "email_verified", True)
            and getattr(ngo, "donation_notifications_enabled", True)
        ]
        for ngo in ngos:
            if not hasattr(ngo, "distance_km") and donation.restaurant_lat is not None and donation.restaurant_lng is not None:
                ngo.distance_km = LocationService.haversine_distance_km(
                    donation.restaurant_lat,
                    donation.restaurant_lng,
                    ngo.current_lat,
                    ngo.current_lng,
                )
        logger.info(
            "Radius reevaluation donation=%s rule=%skm db_distance=%s ngos=%s",
            donation.id,
            radius_km,
            used_db_distance,
            len(ngos),
        )
        return ngos

    def _withdraw_out_of_radius_notifications(self, donation, eligible_ids):
        return DonationNotificationLog.objects.filter(
            donation=donation,
            is_active=True,
            status__in=list(self.ACTIVE_NOTIFICATION_STATUSES),
        ).exclude(ngo_id__in=list(eligible_ids)).update(
            status="withdrawn",
            is_active=False,
            responded_at=timezone.now(),
        )

    def _upsert_notification_and_send(self, *, donation, ngo, radius_km, distance_km, message, template_data, trigger):
        notification, created = DonationNotificationLog.objects.get_or_create(
            donation=donation,
            ngo=ngo,
            defaults={
                "status": "pending",
                "radius_km": radius_km,
                "distance_km": distance_km,
                "sms_provider_response": {},
                "email_provider_response": {},
                "is_active": True,
                "debug_context": {"trigger": trigger},
            },
        )

        if created:
            result = self._send_notification_sms(ngo, message, template_data)
            email_result = self._send_notification_email(donation, ngo)
            notification.radius_km = radius_km
            notification.distance_km = distance_km
            notification.sms_provider_response = result
            notification.email_provider_response = email_result
            notification.email_status = email_result.get("status", "failed")
            notification.email_sent_at = timezone.now() if notification.email_status == "sent" else None
            notification.email_error = email_result.get("reason", "")
            notification.status = "sent" if result.get("status") in {"accepted", "skipped"} else "failed"
            notification.is_active = notification.status != "failed"
            notification.debug_context = {
                "trigger": trigger,
                "radius_km": radius_km,
                "distance_km": distance_km,
            }
            notification.save(
                update_fields=[
                    "radius_km",
                    "distance_km",
                    "sms_provider_response",
                    "email_status",
                    "email_sent_at",
                    "email_provider_response",
                    "email_error",
                    "status",
                    "is_active",
                    "debug_context",
                ]
            )
            return {"ngo_id": ngo.id, "phone": ngo.phone, "email": ngo.user.email, **result, "email_result": email_result}

        notification.radius_km = radius_km
        notification.distance_km = distance_km
        notification.responded_at = None
        notification.debug_context = {
            "trigger": trigger,
            "radius_km": radius_km,
            "distance_km": distance_km,
        }
        if notification.status in {"withdrawn", "expired", "failed"}:
            result = self._send_notification_sms(ngo, message, template_data)
            notification.sms_provider_response = result
            notification.status = "sent" if result.get("status") in {"accepted", "skipped"} else "failed"
            notification.is_active = notification.status != "failed"
            notification.notified_at = timezone.now()
        elif notification.status in self.ACTIVE_NOTIFICATION_STATUSES:
            notification.is_active = True
        else:
            notification.is_active = False
        if notification.email_status == "not_sent" and notification.email_sent_at is None:
            email_result = self._send_notification_email(donation, ngo)
            notification.email_provider_response = email_result
            notification.email_status = email_result.get("status", "failed")
            notification.email_sent_at = timezone.now() if notification.email_status == "sent" else None
            notification.email_error = email_result.get("reason", "")
        notification.save(
            update_fields=[
                "radius_km",
                "distance_km",
                "is_active",
                "responded_at",
                "debug_context",
                "sms_provider_response",
                "email_status",
                "email_sent_at",
                "email_provider_response",
                "email_error",
                "status",
                "notified_at",
            ]
        )
        return {
            "ngo_id": ngo.id,
            "phone": ngo.phone,
            "email": ngo.user.email,
            "status": "existing",
            "radius_km": radius_km,
            "distance_km": distance_km,
            "email_status": notification.email_status,
        }

    def _send_notification_sms(self, ngo, message, template_data):
        try:
            return send_sms(ngo.phone, message, template_data=template_data)
        except requests.RequestException:
            logger.exception(
                "Failed to send surplus SMS to NGO %s for donation flow",
                ngo.id,
            )
            return {"status": "failed", "reason": "request-error"}

    def _send_notification_email(self, donation, ngo):
        try:
            return send_surplus_donation_email(donation, ngo)
        except Exception as exc:
            logger.exception(
                "Failed to send donation email donation=%s ngo=%s",
                donation.id,
                ngo.id,
            )
            return {"status": "failed", "reason": str(exc)}


def notify_nearby_ngos_about_surplus(surplus_request):
    if not getattr(settings, "DONATION_NOTIFICATIONS_ASYNC", True):
        return DonationNotificationService().process_new_donation(surplus_request)

    donation_id = surplus_request.id

    def enqueue_notification_job():
        _notification_executor.submit(_process_donation_notification_job, donation_id)

    transaction.on_commit(enqueue_notification_job)
    return [{"donation_id": donation_id, "status": "queued"}]


def _process_donation_notification_job(donation_id):
    try:
        donation = SurplusFoodRequest.objects.select_related("restaurant").get(id=donation_id)
    except SurplusFoodRequest.DoesNotExist:
        logger.warning("Skipped donation notification job; donation %s no longer exists.", donation_id)
        return []

    try:
        return DonationNotificationService().process_new_donation(donation)
    except Exception:
        logger.exception("Donation notification job failed for donation=%s", donation_id)
        return []
