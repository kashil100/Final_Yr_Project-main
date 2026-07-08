from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from donations.models import (
    NGOGalleryImage,
    RestaurantProfile,
    VolunteerProfile,
    NGOProfile,
    SurplusFoodRequest,
    PickupTask,
    NGOFoodRequest,
)
from donations.services import notify_nearby_ngos_about_surplus
from donations.services.location_service import LocationService
from donations.services.notification_service import DonationNotificationService
from donations.services.transparency_service import (
    TRANSPARENCY_PROOF_PENDING_HOURS,
    get_ngo_delivery_filter,
    get_ngo_gallery_queryset,
    get_pending_transparency_deliveries,
    mark_delivery_awaiting_transparency_proof,
    mark_delivery_proof_uploaded,
)
from donations.services.trust_score_service import (
    TRUST_SCORE_BLOCK_THRESHOLD,
    VOLUNTEER_FLAG_THRESHOLD,
    get_delivery_visibility_limit,
    get_trust_status_tone,
    monitor_suspicious_deliveries,
    record_successful_delivery_confirmation,
)
import requests
from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.conf import settings
from django.utils.dateparse import parse_datetime
import json
import random
from collections import OrderedDict

CSR_CERTIFICATE_THRESHOLD = getattr(settings, "CSR_CERTIFICATE_THRESHOLD", 10)
VOLUNTEER_MONTHLY_CERTIFICATE_TARGET = 10
notification_service = DonationNotificationService()
VOLUNTEER_SUSPENDED_MESSAGE = (
    "Your volunteer account has been suspended due to repeated failed deliveries."
)
VOLUNTEER_VERIFICATION_REQUIRED_MESSAGE = (
    "Your volunteer account is not Aadhaar verified. Please complete verification to continue."
)
def _generate_delivery_otp():
    return f"{random.randint(0, 999999):06d}"


def _ensure_task_otp(pickup_task):
    if pickup_task and not pickup_task.delivery_otp:
        pickup_task.delivery_otp = _generate_delivery_otp()
        pickup_task.save(update_fields=["delivery_otp"])
    return pickup_task


def _parse_local_datetime(value):
    parsed = parse_datetime(value) if value else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _decorate_safety(item):
    status = getattr(item, "safety_status", "Unknown")
    if status == "Safe":
        item.safety_badge_class = "status-complete"
        item.safety_icon = "fa-check"
    elif status == "Expiring Soon":
        item.safety_badge_class = "status-pending"
        item.safety_icon = "fa-triangle-exclamation"
    elif status == "Expired":
        item.safety_badge_class = "status-danger"
        item.safety_icon = "fa-ban"
    else:
        item.safety_badge_class = "status-neutral"
        item.safety_icon = "fa-circle-question"
    return item


def _handle_ngo_gallery_upload(request, profile, redirect_name):
    image_file = request.FILES.get("image")
    caption = (request.POST.get("caption") or "").strip()
    pickup_task_id = request.POST.get("pickup_task_id")

    if not image_file:
        messages.error(request, "Please choose an image to upload.")
        return redirect(redirect_name)

    if not str(getattr(image_file, "content_type", "")).startswith("image/"):
        messages.error(request, "Only image uploads are allowed.")
        return redirect(redirect_name)

    if not pickup_task_id:
        messages.error(request, "Please choose a completed delivery for this transparency proof.")
        return redirect(redirect_name)

    try:
        pickup_task = PickupTask.objects.get(
            get_ngo_delivery_filter(profile),
            id=pickup_task_id,
            completed=True,
        )
    except PickupTask.DoesNotExist:
        messages.error(request, "That delivery is not available for proof upload.")
        return redirect(redirect_name)

    gallery_item = NGOGalleryImage(
        ngo=profile,
        pickup_task=pickup_task,
        image=image_file,
        caption=caption,
    )
    try:
        gallery_item.full_clean()
        gallery_item.save()
    except ValidationError as exc:
        for error_list in getattr(exc, "message_dict", {}).values():
            for error in error_list:
                messages.error(request, error)
        for error in exc.messages:
            messages.error(request, error)
    else:
        mark_delivery_proof_uploaded(pickup_task)
        messages.success(request, "Gallery photo uploaded successfully.")

    return redirect(redirect_name)


def _clean_delivery_proof(pickup, proof_file):
    if not proof_file:
        return None
    if not str(getattr(proof_file, "content_type", "")).startswith("image/"):
        raise ValidationError("Only image proof uploads are allowed.")
    return pickup._meta.get_field("delivery_proof_image").clean(proof_file, pickup)


def _add_surplus_notification_message(request, results):
    if not results:
        messages.info(
            request,
            "Donation posted, but no NGO profiles with phone numbers were found in the same city.",
        )
        return

    queued_count = sum(1 for result in results if result.get("status") == "queued")
    if queued_count:
        messages.success(
            request,
            "Donation posted. Eligible NGOs will be notified by email and dashboard alert in the background.",
        )
        return

    accepted_count = sum(1 for result in results if result.get("status") == "accepted")
    skipped_count = sum(1 for result in results if result.get("status") == "skipped")
    failed_count = sum(1 for result in results if result.get("status") == "failed")
    skipped_reasons = {result.get("reason") for result in results if result.get("status") == "skipped"}

    if accepted_count:
        messages.success(
            request,
            f"Donation posted and the SMS provider accepted notification requests for {accepted_count} NGO contact(s). "
            "Phone delivery can still fail later if the provider template, sender ID, or DLT setup is incorrect.",
        )

    if skipped_count:
        if skipped_reasons == {"console-backend"}:
            demo_numbers = ", ".join(
                sorted(
                    {
                        str(result.get("phone")).strip()
                        for result in results
                        if str(result.get("phone") or "").strip()
                    }
                )
            )
            messages.success(
                request,
                "Donation posted in demo SMS mode. "
                f"HappyTummy would notify these NGO mobile numbers: {demo_numbers}.",
            )
            return
        if "placeholder-msg91-config" in skipped_reasons or "placeholder-twilio-config" in skipped_reasons:
            messages.warning(
                request,
                "Donation posted, but live SMS is still using example credentials in `.env`. "
                "Replace the sample SMS provider values with your real account credentials, then restart the Django server.",
            )
            return
        messages.warning(
            request,
            "Donation posted, but live SMS is not enabled for this platform yet. "
            "NGOs only need their normal mobile numbers. The platform owner must configure the SMS gateway "
            "(recommended: MSG91 for Indian numbers) so HappyTummy can send SMS to those regular phone numbers.",
        )

    if failed_count:
        messages.error(
            request,
            f"Donation posted, but {failed_count} NGO SMS notification request(s) failed immediately.",
        )


def _get_active_volunteer_profile(request):
    try:
        profile = VolunteerProfile.objects.select_related("user").get(user=request.user)
    except VolunteerProfile.DoesNotExist:
        return None, None

    if (
        profile.is_blocked
        or profile.flag_count >= VOLUNTEER_FLAG_THRESHOLD
        or profile.trust_score < TRUST_SCORE_BLOCK_THRESHOLD
        or not profile.user.is_active
    ):
        if not profile.is_blocked:
            profile.block()
        logout(request)
        return None, render(
            request,
            "auth/volunteer_login.html",
            {"error": VOLUNTEER_SUSPENDED_MESSAGE},
        )

    verification_status = getattr(
        profile,
        "verification_status",
        VolunteerProfile.VERIFICATION_STATUS_PENDING,
    )
    if (
        not getattr(profile, "aadhar_verified", False)
        or verification_status != VolunteerProfile.VERIFICATION_STATUS_VERIFIED
    ):
        if profile.user.is_active:
            profile.user.is_active = False
            profile.user.save(update_fields=["is_active"])
        logout(request)
        return None, render(
            request,
            "auth/volunteer_login.html",
            {"error": VOLUNTEER_VERIFICATION_REQUIRED_MESSAGE},
        )

    return profile, None
# ---------------------------
# RESTAURANT DASHBOARD
# ---------------------------
@login_required(login_url="/")
def restaurant_dashboard(request):
    monitor_suspicious_deliveries()
    # notification_service.reevaluate_active_donations()
    try:
        notification_service.reevaluate_active_donations()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Error while reevaluating donations: %s", e)
    try:
        profile = RestaurantProfile.objects.get(user=request.user)
    except RestaurantProfile.DoesNotExist:
        return render(request, "dashboard/restaurant_dashboard.html", {
            "profile": None,
            "error": "No restaurant profile found for this account. Please contact support or re-register."
        })

    # -------------------------------------------------
    # HANDLE POST REQUESTS
    # -------------------------------------------------
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_donation":
            cooked_at = _parse_local_datetime(request.POST.get("cooked_at"))
            expiry_at = _parse_local_datetime(request.POST.get("expiry_at"))
            restaurant_lat, restaurant_lng = LocationService.sync_restaurant_coordinates(profile)
            donation = SurplusFoodRequest.objects.create(
                restaurant=profile,
                food_type=request.POST.get("food_type"),
                quantity=request.POST.get("quantity"),
                cooked_at=cooked_at,
                expiry_at=expiry_at,
                storage_type=request.POST.get("storage_type") or "room_temp",
                safety_notes=request.POST.get("safety_notes") or "",
                restaurant_lat=restaurant_lat,
                restaurant_lng=restaurant_lng,
            )
            notification_results = notify_nearby_ngos_about_surplus(donation)
            _add_surplus_notification_message(request, notification_results)
            return redirect("restaurant_dashboard")

        elif action == "delete_donation":
            donation_id = request.POST.get("donation_id")
            try:
                donation = SurplusFoodRequest.objects.get(
                    id=donation_id,
                    restaurant=profile,
                )
            except SurplusFoodRequest.DoesNotExist:
                messages.error(request, "That donation could not be found.")
                return redirect("restaurant_dashboard")

            if donation.is_picked:
                messages.warning(
                    request,
                    "This donation has already been accepted by an NGO and can no longer be deleted.",
                )
                return redirect("restaurant_dashboard")

            donation.delete()
            messages.success(request, "Donation deleted successfully.")
            return redirect("restaurant_dashboard")

        elif action == "update_profile":
            profile.business_name = request.POST.get("business_name")
            profile.contact_person = request.POST.get("contact_person")
            profile.phone = request.POST.get("phone")

            profile.state = request.POST.get("state")
            profile.district = request.POST.get("district")
            profile.city = request.POST.get("city")


            profile.pincode = request.POST.get("pincode")
            # Only assign taluka if it exists in the model
            if hasattr(profile, "taluka"):
                profile.taluka = request.POST.get("taluka")

            profile.address = request.POST.get("address")

            profile.save()
            return redirect("restaurant_dashboard")

        elif action == "accept_ngo_request":
            ngo_request_id = request.POST.get("ngo_request_id")
            try:
                ngo_request = NGOFoodRequest.objects.get(id=ngo_request_id, fulfilled=False, accepted_by__isnull=True)
                ngo_request.accepted_by = profile
                ngo_request.save()

                # Create PickupTask for this NGO request (restaurant -> NGO)
                pickup_task, created = PickupTask.objects.get_or_create(
                    ngo_request=ngo_request,
                    defaults={"status": PickupTask.STATUS_ACCEPTED},
                )
                if not created and not pickup_task.status:
                    pickup_task.status = PickupTask.STATUS_ACCEPTED
                    pickup_task.save(update_fields=["status"])
                _ensure_task_otp(pickup_task)

                # Notify nearby volunteers (same city)
                city = profile.city
                nearby_volunteers = VolunteerProfile.objects.filter(
                    area__icontains=city,
                    is_available=True,
                    is_blocked=False,
                    user__is_active=True,
                )
                for volunteer in nearby_volunteers:
                    # Placeholder for notification logic (email, SMS, app notification)
                    print(f"Notify volunteer {volunteer.full_name} ({volunteer.phone}) for delivery of NGO food request {ngo_request.id} in {city}")
            except NGOFoodRequest.DoesNotExist:
                pass
            return redirect("restaurant_dashboard")

    # -------------------------------------------------
    # GEOCODING (STRUCTURED — NO OCEAN)
    # -------------------------------------------------
    lat, lng = LocationService.sync_restaurant_coordinates(profile)

    # -------------------------------------------------
    # FALLBACK (ONLY IF API FAILS)
    # -------------------------------------------------
    if not lat or not lng:
        lat, lng = 22.5726, 88.3639  # Kolkata

    # -------------------------------------------------
    # DASHBOARD DATA
    # -------------------------------------------------

    requests_qs = (
        SurplusFoodRequest.objects
        .filter(restaurant=profile)
        .select_related("restaurant")
        .prefetch_related("pickuptask_set")
        .order_by("-timestamp")
    )
    recent_requests = requests_qs[:10]

    for donation_request in recent_requests:
        pickup_task = donation_request.pickuptask_set.first()
        _decorate_safety(donation_request)
        if pickup_task and pickup_task.completed:
            donation_request.status_label = "Delivered"
            donation_request.status_class = "status-complete"
        elif pickup_task and pickup_task.delivered_to_ngo:
            donation_request.status_label = "Reached NGO"
            donation_request.status_class = "status-pending"
        elif pickup_task and pickup_task.assigned_to:
            donation_request.status_label = "Volunteer Assigned"
            donation_request.status_class = "status-pending"
        elif donation_request.is_picked:
            donation_request.status_label = "Accepted by NGO"
            donation_request.status_class = "status-pending"
        else:
            donation_request.status_label = "Awaiting NGO"
            donation_request.status_class = "status-pending"
        donation_request.can_delete = not donation_request.is_picked
        donation_request.active_notifications_count = donation_request.notification_logs.filter(
            is_active=True,
            status__in=["pending", "sent", "read"],
        ).count()
        donation_request.radius_debug_label = donation_request.notification_debug.get("radius_rule", "-")

    total_donations = requests_qs.count()
    completed_pickups = PickupTask.objects.filter(
        request__restaurant=profile,
        request__isnull=False,
        completed=True,
    ).count()
    pending_pickups = total_donations - completed_pickups
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_completed_pickups = PickupTask.objects.filter(
        request__restaurant=profile,
        request__isnull=False,
        completed=True,
    ).filter(
        models.Q(completed_at__gte=month_start)
        | models.Q(completed_at__isnull=True, assigned_at__gte=month_start)
    ).count()
    csr_certificate_eligible = monthly_completed_pickups >= CSR_CERTIFICATE_THRESHOLD
    donations_remaining_for_certificate = max(CSR_CERTIFICATE_THRESHOLD - monthly_completed_pickups, 0)
    # Show NGO food requests in the same city (including ones accepted by this restaurant)
    nearby_ngo_requests = NGOFoodRequest.objects.filter(
        ngo__city__iexact=profile.city
    ).filter(
        models.Q(accepted_by__isnull=True) | models.Q(accepted_by=profile)
    ).select_related('ngo', 'accepted_by')

    # -------------------------------------------------
    # RENDER
    # -------------------------------------------------
    return render(request, "dashboard/restaurant_dashboard.html", {
        "profile": profile,
        "requests": recent_requests,
        "total_donations": total_donations,
        "pending_pickups": pending_pickups,
        "completed_pickups": completed_pickups,
        "lat": lat,
        "lng": lng,
        "nearby_ngo_requests": nearby_ngo_requests,
        "csr_certificate_threshold": CSR_CERTIFICATE_THRESHOLD,
        "csr_certificate_eligible": csr_certificate_eligible,
        "donations_remaining_for_certificate": donations_remaining_for_certificate,
        "csr_completed_donations": monthly_completed_pickups,
        "csr_month_label": now.strftime("%B %Y"),
    })
@login_required(login_url="/")
def restaurant_csr_certificate(request):
    try:
        profile = RestaurantProfile.objects.get(user=request.user)
    except RestaurantProfile.DoesNotExist:
        messages.error(request, "No restaurant profile found for this account.")
        return redirect("restaurant_dashboard")

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    completed_donations = PickupTask.objects.filter(
        request__restaurant=profile,
        request__isnull=False,
        completed=True,
    ).filter(
        models.Q(completed_at__gte=month_start)
        | models.Q(completed_at__isnull=True, assigned_at__gte=month_start)
    ).count()
    if completed_donations < CSR_CERTIFICATE_THRESHOLD:
        messages.warning(
            request,
            f"You need at least {CSR_CERTIFICATE_THRESHOLD} completed donations to unlock the CSR certificate.",
        )
        return redirect("restaurant_dashboard")

    issued_on = timezone.now()
    certificate_id = f"CSR-{issued_on.strftime('%Y%m%d')}-{profile.id:04d}"

    return render(
        request,
        "dashboard/csr_certificate.html",
        {
            "profile": profile,
            "completed_donations": completed_donations,
            "csr_certificate_threshold": CSR_CERTIFICATE_THRESHOLD,
            "issued_on": issued_on,
            "certificate_id": certificate_id,
            "csr_month_label": now.strftime("%B %Y"),
        },
    )


# ---------------------------
# VOLUNTEER DASHBOARD
# ---------------------------
@login_required(login_url="/")
def volunteer_dashboard(request):
    monitor_suspicious_deliveries()
    profile, blocked_response = _get_active_volunteer_profile(request)
    if blocked_response:
        return blocked_response
    if profile is None:
        return redirect("/")
    page_message = ""
    page_message_type = ""

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_profile":
            profile.full_name = request.POST.get("full_name")
            profile.phone = request.POST.get("phone")
            profile.area = request.POST.get("area")
            profile.save()
            return redirect("volunteer_dashboard")

        if action == "accept_pickup":
            pickup_id = request.POST.get("pickup_id")
            try:
                pickup = PickupTask.objects.get(
                    id=pickup_id,
                    assigned_to=None,
                    completed=False,
                    status=PickupTask.STATUS_ACCEPTED,
                )
                pickup.assigned_to = profile
                pickup.status = PickupTask.STATUS_IN_TRANSIT
                pickup.save(update_fields=["assigned_to", "status"])
            except PickupTask.DoesNotExist:
                pass
            return redirect("volunteer_dashboard")

        if action == "complete_pickup":
            pickup_id = request.POST.get("pickup_id")
            entered_otp = (request.POST.get("delivery_otp") or "").strip()
            proof_file = request.FILES.get("delivery_proof")
            try:
                pickup = PickupTask.objects.get(
                    id=pickup_id,
                    assigned_to=profile,
                    completed=False,
                    delivered_to_ngo=False,
                )
                _ensure_task_otp(pickup)
                if entered_otp != pickup.delivery_otp:
                    page_message = "The OTP did not match. Please enter the NGO's delivery code."
                    page_message_type = "error"
                else:
                    cleaned_proof = _clean_delivery_proof(pickup, proof_file)
                    pickup.delivered_to_ngo = True
                    pickup.otp_verified = True
                    pickup.delivered_at = timezone.now()
                    pickup.status = PickupTask.STATUS_DELIVERED
                    if cleaned_proof:
                        pickup.delivery_proof_image = cleaned_proof
                        pickup.proof_uploaded_at = timezone.now()
                    update_fields = ["delivered_to_ngo", "otp_verified", "delivered_at", "status"]
                    if cleaned_proof:
                        update_fields.extend(["delivery_proof_image", "proof_uploaded_at"])
                    pickup.save(update_fields=update_fields)
                    return redirect("volunteer_dashboard")
            except ValidationError as exc:
                page_message = "; ".join(exc.messages)
                page_message_type = "error"
            except PickupTask.DoesNotExist:
                page_message = "That pickup task is no longer available."
                page_message_type = "error"

    my_tasks_qs = (
        PickupTask.objects
        .filter(assigned_to=profile)
        .select_related("request__restaurant", "ngo_request__ngo", "ngo_request__accepted_by")
        .order_by("-assigned_at")
    )
    pending_count = my_tasks_qs.filter(completed=False).count()
    completed_count = my_tasks_qs.filter(completed=True).count()
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_completed_count = my_tasks_qs.filter(
        completed=True,
        assigned_at__gte=month_start,
    ).count()
    monthly_target = VOLUNTEER_MONTHLY_CERTIFICATE_TARGET
    monthly_progress_percent = min(int((monthly_completed_count / monthly_target) * 100), 100) if monthly_target else 100
    monthly_deliveries_left = max(monthly_target - monthly_completed_count, 0)
    monthly_certificate_earned = monthly_completed_count >= monthly_target
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_completed_count = my_tasks_qs.filter(
        completed=True,
        assigned_at__gte=month_start,
    ).count()
    monthly_target = VOLUNTEER_MONTHLY_CERTIFICATE_TARGET
    monthly_progress_percent = min(int((monthly_completed_count / monthly_target) * 100), 100) if monthly_target else 100
    monthly_deliveries_left = max(monthly_target - monthly_completed_count, 0)
    monthly_certificate_earned = monthly_completed_count >= monthly_target
    volunteer_city = profile.area.split(",")[-1].strip() if "," in profile.area else profile.area.strip()

    available_pickups_qs = (
        PickupTask.objects
        .filter(
            assigned_to=None,
            completed=False,
            status=PickupTask.STATUS_ACCEPTED,
        )
        .filter(
            models.Q(request__restaurant__city__iexact=volunteer_city) |
            models.Q(
                ngo_request__accepted_by__city__iexact=volunteer_city,
                ngo_request__fulfilled=False,
            )
        )
        .filter(
            models.Q(request__isnull=True)
            | models.Q(request__expiry_at__isnull=True)
            | models.Q(request__expiry_at__gt=timezone.now())
        )
        .select_related("request__restaurant", "ngo_request__ngo", "ngo_request__accepted_by")
        .order_by("-assigned_at")
    )
    visibility_limit = get_delivery_visibility_limit(profile.trust_score)
    if visibility_limit is not None:
        available_pickups_qs = available_pickups_qs[:visibility_limit]
    available_pickups = list(available_pickups_qs)
    for pickup in available_pickups:
        if pickup.request:
            _decorate_safety(pickup.request)

    my_tasks = list(my_tasks_qs)
    ngo_frequency_map = OrderedDict()
    restaurant_frequency_map = OrderedDict()
    for task in my_tasks:
        if task.request:
            source_address = task.source_address
            destination_address = task.destination_address
            restaurant_name = task.request.restaurant.business_name
            ngo_name = getattr(task.request.accepted_by_ngo, "name", f"NGO in {task.request.restaurant.city}")
        elif task.ngo_request:
            source_address = task.source_address
            destination_address = task.destination_address
            restaurant_name = task.ngo_request.accepted_by.business_name if task.ngo_request.accepted_by else "Unassigned Restaurant"
            ngo_name = task.ngo_request.ngo.name if task.ngo_request.ngo else "Unknown NGO"
        else:
            source_address = "-"
            destination_address = "-"
            restaurant_name = "Unknown Restaurant"
            ngo_name = "Unknown NGO"

        task.source_address_display = source_address
        task.destination_address_display = destination_address

        if not task.completed:
            continue

        if restaurant_name not in restaurant_frequency_map:
            restaurant_frequency_map[restaurant_name] = {"name": restaurant_name, "count": 0}
        restaurant_frequency_map[restaurant_name]["count"] += 1

        if ngo_name not in ngo_frequency_map:
            ngo_frequency_map[ngo_name] = {"name": ngo_name, "count": 0}
        ngo_frequency_map[ngo_name]["count"] += 1

    restaurant_frequency = sorted(
        restaurant_frequency_map.values(),
        key=lambda row: (-row["count"], row["name"].lower()),
    )
    ngo_frequency = sorted(
        ngo_frequency_map.values(),
        key=lambda row: (-row["count"], row["name"].lower()),
    )
    has_active_task = pending_count > 0
    trust_logs = list(profile.trust_score_logs.select_related("pickup_task")[:12])
    trust_status_tone = get_trust_status_tone(profile)

    return render(request, "dashboard/volunteer_dashboard.html", {
        "profile": profile,
        "tasks": my_tasks,
        "pending_count": pending_count,
        "completed_count": completed_count,
        "available_pickups": available_pickups,
        "volunteer_city": volunteer_city,
        "has_active_task": has_active_task,
        "page_message": page_message,
        "page_message_type": page_message_type,
         "monthly_completed_count": monthly_completed_count,
        "monthly_target": monthly_target,
        "monthly_progress_percent": monthly_progress_percent,
        "monthly_deliveries_left": monthly_deliveries_left,
        "monthly_certificate_earned": monthly_certificate_earned,
        "monthly_label": now.strftime("%B %Y"),
        "ngo_frequency": ngo_frequency,
        "restaurant_frequency": restaurant_frequency,
        "ngo_frequency_count": len(ngo_frequency),
        "restaurant_frequency_count": len(restaurant_frequency),
        "trust_logs": trust_logs,
        "trust_status_tone": trust_status_tone,
        "delivery_visibility_limit": visibility_limit,
    })


@login_required(login_url="/")
def volunteer_monthly_certificate(request):
    monitor_suspicious_deliveries()
    profile, blocked_response = _get_active_volunteer_profile(request)
    if blocked_response:
        return blocked_response
    if profile is None:
        return redirect("/")
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_completed_count = PickupTask.objects.filter(
        assigned_to=profile,
        completed=True,
        assigned_at__gte=month_start,
    ).count()
    monthly_target = VOLUNTEER_MONTHLY_CERTIFICATE_TARGET
    monthly_certificate_earned = monthly_completed_count >= monthly_target

    if not monthly_certificate_earned:
        messages.warning(
            request,
            f"Complete {max(monthly_target - monthly_completed_count, 0)} more delivery(ies) this month to unlock your certificate.",
        )
        return redirect("volunteer_dashboard")

    return render(request, "dashboard/volunteer_certificate.html", {
        "profile": profile,
        "monthly_label": now.strftime("%B %Y"),
        "monthly_completed_count": monthly_completed_count,
        "monthly_target": monthly_target,
        "issued_on": now,
    })
# ---------------------------
# NGO DASHBOARD
# ---------------------------
@login_required(login_url="/")
def ngo_dashboard(request):
    monitor_suspicious_deliveries()
    notification_service.reevaluate_active_donations()
    try:
        profile = NGOProfile.objects.get(user=request.user)
    except NGOProfile.DoesNotExist:
        return render(request, "dashboard/ngo_dashboard.html", {
            "profile": None,
            "error": "No NGO profile found for this account. Please contact support or re-register."
        })

    # Own food requests by this NGO
    from donations.models import NGOFoodRequest
    my_food_requests = NGOFoodRequest.objects.filter(ngo=profile).order_by('-timestamp')

    # -------------------------------------------------
    # HANDLE POST REQUESTS
    # -------------------------------------------------
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_profile":
            profile.name = request.POST.get("name")
            profile.contact_person = request.POST.get("contact_person")
            profile.phone = request.POST.get("phone")
            profile.address = request.POST.get("address")
            profile.city = request.POST.get("city")

            profile.save()
            return redirect("ngo_dashboard")

        elif action == "accept_donation":
            donation_id = request.POST.get("donation_id")
            try:
                donation = SurplusFoodRequest.objects.get(id=donation_id, is_picked=False)
                if not donation.is_safe_to_accept:
                    return redirect("ngo_dashboard")
                accepted = notification_service.accept_donation(donation, profile)
                if not accepted:
                    messages.error(request, "This donation is no longer available for your NGO.")
                    return redirect("ngo_dashboard")

                # Create PickupTask for this surplus food (restaurant -> NGO)
                pickup_task, created = PickupTask.objects.get_or_create(
                    request=donation,
                    defaults={"status": PickupTask.STATUS_ACCEPTED},
                )
                if not created and not pickup_task.status:
                    pickup_task.status = PickupTask.STATUS_ACCEPTED
                    pickup_task.save(update_fields=["status"])
                _ensure_task_otp(pickup_task)

                # Notify nearby volunteers (same city)
                city = donation.restaurant.city
                nearby_volunteers = VolunteerProfile.objects.filter(
                    area__icontains=city,
                    is_available=True,
                    is_blocked=False,
                    user__is_active=True,
                )
                for volunteer in nearby_volunteers:
                    # Placeholder for notification logic (email, SMS, app notification)
                    print(f"Notify volunteer {volunteer.full_name} ({volunteer.phone}) for delivery of food request {donation.id} in {city}")
            except SurplusFoodRequest.DoesNotExist:
                pass
            return redirect("ngo_dashboard")

        elif action == "request_food":
            food_type = request.POST.get("food_type")
            quantity = request.POST.get("quantity")
            if food_type and quantity:
                NGOFoodRequest.objects.create(
                    ngo=profile,
                    food_type=food_type,
                    quantity=quantity,
                    fulfilled=False
                )
            return redirect("ngo_dashboard")

        elif action == "confirm_reached":
            pickup_id = request.POST.get("pickup_id")
            try:
                pickup = PickupTask.objects.get(
                    id=pickup_id,
                    delivered_to_ngo=True,
                    completed=False,
                )
                if pickup.request:
                    is_allowed = pickup.request.accepted_by_ngo_id == profile.id
                elif pickup.ngo_request:
                    is_allowed = pickup.ngo_request.ngo_id == profile.id
                else:
                    is_allowed = False

                if not is_allowed:
                    raise PickupTask.DoesNotExist

                pickup.completed = True
                pickup.completed_at = timezone.now()
                pickup.ngo_confirmed_at = timezone.now()
                pickup.status = PickupTask.STATUS_DELIVERED
                pickup.save(update_fields=["completed", "completed_at", "ngo_confirmed_at", "status"])
                mark_delivery_awaiting_transparency_proof(pickup)
                if pickup.assigned_to_id:
                    record_successful_delivery_confirmation(pickup.assigned_to, pickup)

                if pickup.ngo_request:
                    pickup.ngo_request.fulfilled = True
                    pickup.ngo_request.save(update_fields=["fulfilled"])
            except PickupTask.DoesNotExist:
                pass
            return redirect("ngo_dashboard")

        elif action == "upload_gallery_image":
            return _handle_ngo_gallery_upload(request, profile, "ngo_gallery")

    # -------------------------------------------------
    # GEOCODING (STRUCTURED — NO OCEAN)
    # -------------------------------------------------
    lat, lng = LocationService.sync_ngo_coordinates(profile)

    # -------------------------------------------------
    # FALLBACK (ONLY IF API FAILS)
    # -------------------------------------------------
    if not lat or not lng:
        lat, lng = 22.5726, 88.3639  # Kolkata

    # -------------------------------------------------
    # DASHBOARD DATA
    # -------------------------------------------------
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # New/unpicked donations in the NGO's city (for this NGO to view/claim)
    new_donations = [_decorate_safety(donation) for donation in notification_service.get_dashboard_notifications(profile) if donation.is_safe_to_accept]
    notification_service.mark_notifications_as_read(profile)

    # Pending pickups: accepted by NGO and either waiting for a volunteer or currently in transit.
    pending_pickups = PickupTask.objects.filter(
        models.Q(request__accepted_by_ngo=profile) |
        models.Q(ngo_request__ngo=profile),
        completed=False,
    ).select_related('request', 'request__restaurant', 'ngo_request', 'ngo_request__accepted_by', 'assigned_to')

    # Completed pickups: food delivered by volunteer
    completed_pickups = PickupTask.objects.filter(
        models.Q(request__accepted_by_ngo=profile) |
        models.Q(ngo_request__ngo=profile),
        completed=True,
        assigned_to__isnull=False,
    ).select_related('request', 'request__restaurant', 'ngo_request', 'ngo_request__accepted_by', 'assigned_to')

    # Recent food pickups (accepted donations in city, including in-progress)
    recent_food_pickups = PickupTask.objects.filter(
        models.Q(request__accepted_by_ngo=profile) |
        models.Q(ngo_request__ngo=profile)
    ).select_related(
        'request',
        'request__restaurant',
        'ngo_request',
        'ngo_request__ngo',
        'ngo_request__accepted_by',
        'assigned_to',
    ).order_by("-assigned_at")[:10]

    for pickup in recent_food_pickups:
        _ensure_task_otp(pickup)
        if pickup.request:
            _decorate_safety(pickup.request)

    total_food_received = completed_pickups.count()
    pending_distributions = pending_pickups.count()
    completed_distributions = total_food_received
    pending_transparency_deliveries = get_pending_transparency_deliveries(profile)
    pending_transparency_count = pending_transparency_deliveries.count()

    # Monthly breakdown by restaurant for completed deliveries.
    monthly_breakdown = PickupTask.objects.filter(
        models.Q(
            completed=True,
            request__isnull=False,
            request__accepted_by_ngo=profile,
            request__timestamp__gte=month_start,
        ) |
        models.Q(
            completed=True,
            ngo_request__isnull=False,
            ngo_request__ngo=profile,
            ngo_request__timestamp__gte=month_start,
        )
    ).annotate(
        restaurant_name=Coalesce(
            "request__restaurant__business_name",
            "ngo_request__accepted_by__business_name",
            "request__restaurant__user__username",
            "ngo_request__accepted_by__user__username",
        )
    ).values("restaurant_name").annotate(
        total_qty=models.Sum(
            models.Case(
                models.When(request__isnull=False, then="request__quantity"),
                models.When(ngo_request__isnull=False, then="ngo_request__quantity"),
                default=0,
                output_field=models.IntegerField(),
            )
        )
    ).order_by("-total_qty")

    monthly_labels = [row["restaurant_name"] or "Unknown" for row in monthly_breakdown]
    monthly_values = [row["total_qty"] or 0 for row in monthly_breakdown]
    # -------------------------------------------------
    # RENDER
    # -------------------------------------------------
    return render(request, "dashboard/ngo_dashboard.html", {
        "profile": profile,
        "pending_pickups": pending_pickups,
        "completed_pickups": completed_pickups,
        "new_donations": new_donations,
        "notification_unread_count": 0,
        "total_food_received": total_food_received,
        "pending_distributions": pending_distributions,
        "completed_distributions": completed_distributions,
        "monthly_labels": json.dumps(monthly_labels),
        "monthly_values": json.dumps(monthly_values),
        "month_label": now.strftime("%B %Y"),
        "recent_food_pickups": recent_food_pickups,
        "lat": lat,
        "lng": lng,
        "my_food_requests": my_food_requests,
        "pending_transparency_count": pending_transparency_count,
    })


@login_required(login_url="/")
def ngo_gallery(request):
    try:
        profile = NGOProfile.objects.get(user=request.user)
    except NGOProfile.DoesNotExist:
        return render(request, "dashboard/ngo_gallery.html", {
            "profile": None,
            "error": "No NGO profile found for this account. Please contact support or re-register."
        })

    if request.method == "POST" and request.POST.get("action") == "upload_gallery_image":
        return _handle_ngo_gallery_upload(request, profile, "ngo_gallery")

    pending_transparency_deliveries = get_pending_transparency_deliveries(profile)
    gallery_qs = get_ngo_gallery_queryset(profile)
    gallery_page = Paginator(gallery_qs, 9).get_page(request.GET.get("gallery_page"))

    return render(request, "dashboard/ngo_gallery.html", {
        "profile": profile,
        "gallery_page": gallery_page,
        "pending_transparency_deliveries": pending_transparency_deliveries,
        "pending_transparency_count": pending_transparency_deliveries.count(),
        "transparency_proof_pending_hours": TRANSPARENCY_PROOF_PENDING_HOURS,
    })


@login_required(login_url="/")
@require_POST
def volunteer_location_update(request):
    monitor_suspicious_deliveries()
    profile, blocked_response = _get_active_volunteer_profile(request)
    if blocked_response:
        return JsonResponse({"success": False, "error": VOLUNTEER_SUSPENDED_MESSAGE}, status=403)
    if profile is None:
        return JsonResponse({"success": False, "error": "Volunteer profile not found."}, status=404)
    try:
        profile = VolunteerProfile.objects.get(pk=profile.pk)
    except VolunteerProfile.DoesNotExist:
        return JsonResponse({"success": False, "error": "Volunteer profile not found."}, status=404)

    try:
        lat = float(request.POST.get("lat"))
        lng = float(request.POST.get("lng"))
    except (TypeError, ValueError):
        return JsonResponse({"success": False, "error": "Invalid latitude or longitude."}, status=400)

    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        return JsonResponse({"success": False, "error": "Coordinates out of range."}, status=400)

    profile.current_lat = lat
    profile.current_lng = lng
    profile.location_updated_at = timezone.now()
    profile.save(update_fields=["current_lat", "current_lng", "location_updated_at"])

    return JsonResponse({"success": True})


@login_required(login_url="/")
@require_GET
def ngo_live_volunteer_locations(request):
    try:
        ngo_profile = NGOProfile.objects.get(user=request.user)
    except NGOProfile.DoesNotExist:
        return JsonResponse({"success": False, "error": "NGO profile not found."}, status=404)

    # Volunteers currently assigned to in-progress tasks for this NGO.
    pending_tasks = PickupTask.objects.filter(
        request__isnull=False,
        request__accepted_by_ngo=ngo_profile,
        completed=False,
        assigned_to__isnull=False,
    ).select_related("assigned_to", "request", "request__restaurant")

    volunteer_locations = []
    for task in pending_tasks:
        volunteer = task.assigned_to
        if not volunteer or volunteer.current_lat is None or volunteer.current_lng is None:
            continue

        volunteer_locations.append({
            "task_id": task.id,
            "volunteer_id": volunteer.id,
            "volunteer_name": volunteer.full_name,
            "phone": volunteer.phone,
            "lat": volunteer.current_lat,
            "lng": volunteer.current_lng,
            "updated_at": volunteer.location_updated_at.isoformat() if volunteer.location_updated_at else None,
            "food_type": task.request.food_type,
            "quantity": task.request.quantity,
            "pickup_from": task.request.restaurant.business_name,
        })

    return JsonResponse({"success": True, "locations": volunteer_locations})
