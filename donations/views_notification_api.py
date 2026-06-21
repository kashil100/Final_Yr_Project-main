from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from donations.models import NGOProfile, RestaurantProfile, SurplusFoodRequest
from donations.services.notification_service import DonationNotificationService


notification_service = DonationNotificationService()


@login_required(login_url="/")
@require_GET
def ngo_notification_feed(request):
    try:
        profile = NGOProfile.objects.get(user=request.user)
    except NGOProfile.DoesNotExist:
        return JsonResponse({"success": False, "error": "NGO profile not found."}, status=404)

    notification_service.reevaluate_active_donations()
    payload = notification_service.get_dashboard_payload(profile)
    return JsonResponse({"success": True, **payload})


@login_required(login_url="/")
@require_POST
def ngo_mark_notifications_read(request):
    try:
        profile = NGOProfile.objects.get(user=request.user)
    except NGOProfile.DoesNotExist:
        return JsonResponse({"success": False, "error": "NGO profile not found."}, status=404)

    notification_service.mark_notifications_as_read(profile)
    return JsonResponse({"success": True})


@login_required(login_url="/")
@require_GET
def restaurant_donation_status_feed(request):
    try:
        profile = RestaurantProfile.objects.get(user=request.user)
    except RestaurantProfile.DoesNotExist:
        return JsonResponse({"success": False, "error": "Restaurant profile not found."}, status=404)

    notification_service.reevaluate_active_donations()
    donations = (
        SurplusFoodRequest.objects.filter(restaurant=profile)
        .order_by("-timestamp")
        .prefetch_related("notification_logs")[:20]
    )
    payload = []
    for donation in donations:
        payload.append(
            {
                "id": donation.id,
                "food_type": donation.food_type,
                "quantity": donation.quantity,
                "time_remaining_readable": donation.time_remaining_readable,
                "current_radius_km": donation.current_radius_km,
                "donation_status": donation.donation_status,
                "active_notifications_count": donation.notification_logs.filter(
                    is_active=True,
                    status__in=["pending", "sent", "read"],
                ).count(),
            }
        )
    return JsonResponse({"success": True, "donations": payload})
