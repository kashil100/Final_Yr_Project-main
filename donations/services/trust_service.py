from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from donations.models import PickupTask, VolunteerProfile, VolunteerTrustFlag

VOLUNTEER_FLAG_THRESHOLD = 5
SUSPICIOUS_DELIVERY_AFTER_HOURS = 24
DEFAULT_FLAG_REASON = (
    "Delivery remained unconfirmed for more than 24 hours after volunteer assignment."
)


def monitor_suspicious_deliveries(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(hours=SUSPICIOUS_DELIVERY_AFTER_HOURS)
    suspicious_tasks = (
        PickupTask.objects
        .filter(
            assigned_to__isnull=False,
            completed=False,
            delivered_to_ngo=False,
            flagged_at__isnull=True,
            assigned_at__lte=cutoff,
            status__in=[PickupTask.STATUS_ACCEPTED, PickupTask.STATUS_IN_TRANSIT],
        )
        .select_related("assigned_to", "assigned_to__user")
    )

    flagged_count = 0
    for pickup in suspicious_tasks:
        volunteer = pickup.assigned_to
        if not volunteer:
            continue

        with transaction.atomic():
            locked_pickup = (
                PickupTask.objects
                .select_for_update()
                .select_related("assigned_to", "assigned_to__user")
                .get(pk=pickup.pk)
            )
            if (
                locked_pickup.flagged_at
                or locked_pickup.completed
                or locked_pickup.delivered_to_ngo
                or locked_pickup.assigned_at > cutoff
                or locked_pickup.assigned_to_id is None
            ):
                continue

            locked_pickup.status = PickupTask.STATUS_SUSPICIOUS
            locked_pickup.flagged_at = now
            locked_pickup.flag_reason = DEFAULT_FLAG_REASON
            locked_pickup.save(update_fields=["status", "flagged_at", "flag_reason"])

            VolunteerTrustFlag.objects.create(
                volunteer=locked_pickup.assigned_to,
                pickup_task=locked_pickup,
                reason=DEFAULT_FLAG_REASON,
            )

            VolunteerProfile.objects.filter(pk=locked_pickup.assigned_to_id).update(
                flag_count=F("flag_count") + 1,
                is_available=False,
            )
            volunteer = locked_pickup.assigned_to
            volunteer.refresh_from_db(fields=["flag_count", "is_blocked", "blocked_at", "is_available"])
            if volunteer.flag_count >= VOLUNTEER_FLAG_THRESHOLD and not volunteer.is_blocked:
                volunteer.block(blocked_at=now)

        flagged_count += 1

    return flagged_count
