from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from donations.models import (
    PickupTask,
    VolunteerProfile,
    VolunteerTrustFlag,
    VolunteerTrustScoreLog,
)

TRUST_SCORE_SUCCESSFUL_DELIVERY = 2
TRUST_SCORE_NGO_CONFIRMATION = 5
TRUST_SCORE_FAILED_DELIVERY = -10
TRUST_SCORE_SUSPICIOUS_DELIVERY = -20
TRUST_SCORE_BLOCK_THRESHOLD = 20
VOLUNTEER_FLAG_THRESHOLD = 5
SUSPICIOUS_DELIVERY_AFTER_HOURS = 24


def get_delivery_visibility_limit(trust_score):
    if trust_score >= 80:
        return None
    if trust_score >= 60:
        return 12
    if trust_score >= 40:
        return 6
    return 3


def get_trust_status_tone(profile):
    if profile.is_blocked:
        return "danger"
    if profile.trust_score >= 80:
        return "complete"
    if profile.trust_score >= 60:
        return "pending"
    if profile.trust_score >= 40:
        return "pending"
    return "danger"


def _log_score_change(volunteer, pickup_task, score_change, reason):
    VolunteerTrustScoreLog.objects.create(
        volunteer=volunteer,
        pickup_task=pickup_task,
        score_change=score_change,
        reason=reason,
    )


def _block_if_needed(volunteer, blocked_at=None):
    if volunteer.is_blocked:
        return False
    if volunteer.flag_count >= VOLUNTEER_FLAG_THRESHOLD or volunteer.trust_score < TRUST_SCORE_BLOCK_THRESHOLD:
        volunteer.block(blocked_at=blocked_at)
        return True
    return False


def apply_trust_score_change(
    volunteer,
    *,
    score_change,
    reason,
    pickup_task=None,
    successful_delta=0,
    failed_delta=0,
    suspicious_delta=0,
    ngo_confirmation_delta=0,
    flag_delta=0,
    mark_unavailable=False,
    blocked_at=None,
):
    with transaction.atomic():
        locked_volunteer = VolunteerProfile.objects.select_for_update().get(pk=volunteer.pk)
        locked_volunteer.trust_score = max(0, locked_volunteer.trust_score + score_change)
        if successful_delta:
            locked_volunteer.successful_deliveries += successful_delta
        if failed_delta:
            locked_volunteer.failed_deliveries += failed_delta
        if suspicious_delta:
            locked_volunteer.suspicious_deliveries += suspicious_delta
        if ngo_confirmation_delta:
            locked_volunteer.ngo_confirmations += ngo_confirmation_delta
        if flag_delta:
            locked_volunteer.flag_count += flag_delta
        if mark_unavailable:
            locked_volunteer.is_available = False
        locked_volunteer.save(
            update_fields=[
                "trust_score",
                "successful_deliveries",
                "failed_deliveries",
                "suspicious_deliveries",
                "ngo_confirmations",
                "flag_count",
                "is_available",
            ]
        )
        _log_score_change(locked_volunteer, pickup_task, score_change, reason)
        _block_if_needed(locked_volunteer, blocked_at=blocked_at)
        locked_volunteer.refresh_from_db()
        return locked_volunteer


def record_successful_delivery_confirmation(volunteer, pickup_task):
    updated = apply_trust_score_change(
        volunteer,
        score_change=TRUST_SCORE_SUCCESSFUL_DELIVERY,
        reason="Successful delivery completed",
        pickup_task=pickup_task,
        successful_delta=1,
    )
    return apply_trust_score_change(
        updated,
        score_change=TRUST_SCORE_NGO_CONFIRMATION,
        reason="NGO confirmed delivery",
        pickup_task=pickup_task,
        ngo_confirmation_delta=1,
    )


def record_failed_delivery(volunteer, pickup_task, reason="Failed delivery"):
    return apply_trust_score_change(
        volunteer,
        score_change=TRUST_SCORE_FAILED_DELIVERY,
        reason=reason,
        pickup_task=pickup_task,
        failed_delta=1,
        mark_unavailable=True,
    )


def record_suspicious_delivery(volunteer, pickup_task, reason):
    return apply_trust_score_change(
        volunteer,
        score_change=TRUST_SCORE_SUSPICIOUS_DELIVERY,
        reason=reason,
        pickup_task=pickup_task,
        suspicious_delta=1,
        flag_delta=1,
        mark_unavailable=True,
        blocked_at=timezone.now(),
    )


def monitor_suspicious_deliveries(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(hours=SUSPICIOUS_DELIVERY_AFTER_HOURS)
    suspicious_tasks = (
        PickupTask.objects
        .filter(
            assigned_to__isnull=False,
            completed=False,
            flagged_at__isnull=True,
            assigned_at__lte=cutoff,
            status__in=[
                PickupTask.STATUS_ACCEPTED,
                PickupTask.STATUS_IN_TRANSIT,
                PickupTask.STATUS_DELIVERED,
            ],
        )
        .select_related("assigned_to", "assigned_to__user")
    )

    flagged_count = 0
    reason = "Suspicious undelivered assignment older than 24 hours without NGO confirmation."
    for pickup in suspicious_tasks:
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
                or locked_pickup.assigned_at > cutoff
                or locked_pickup.assigned_to_id is None
            ):
                continue

            locked_pickup.status = PickupTask.STATUS_SUSPICIOUS
            locked_pickup.flagged_at = now
            locked_pickup.flag_reason = reason
            locked_pickup.save(update_fields=["status", "flagged_at", "flag_reason"])
            VolunteerTrustFlag.objects.create(
                volunteer=locked_pickup.assigned_to,
                pickup_task=locked_pickup,
                reason=reason,
            )
            record_suspicious_delivery(locked_pickup.assigned_to, locked_pickup, reason)
        flagged_count += 1

    return flagged_count
