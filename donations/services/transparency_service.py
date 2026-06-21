from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from donations.models import NGOGalleryImage, PickupTask

TRANSPARENCY_PROOF_PENDING_HOURS = getattr(
    settings,
    "TRANSPARENCY_PROOF_PENDING_HOURS",
    24,
)


def get_ngo_delivery_filter(ngo_profile):
    return Q(request__accepted_by_ngo=ngo_profile) | Q(ngo_request__ngo=ngo_profile)


def get_pending_transparency_deliveries(ngo_profile):
    cutoff = timezone.now() - timedelta(hours=TRANSPARENCY_PROOF_PENDING_HOURS)
    return (
        PickupTask.objects
        .filter(
            get_ngo_delivery_filter(ngo_profile),
            completed=True,
        )
        .filter(
            Q(transparency_status=PickupTask.TRANSPARENCY_STATUS_AWAITING_PROOF)
            | Q(
                transparency_status=PickupTask.TRANSPARENCY_STATUS_DELIVERED,
                ngo_confirmed_at__lte=cutoff,
            )
            | Q(
                transparency_status=PickupTask.TRANSPARENCY_STATUS_DELIVERED,
                distribution_proof_images__isnull=True,
            )
        )
        .select_related(
            "request",
            "request__restaurant",
            "ngo_request",
            "ngo_request__ngo",
            "ngo_request__accepted_by",
        )
        .distinct()
        .order_by("-ngo_confirmed_at", "-completed_at", "-assigned_at")
    )


def get_ngo_gallery_queryset(ngo_profile):
    return (
        NGOGalleryImage.objects
        .filter(ngo=ngo_profile)
        .select_related(
            "pickup_task",
            "pickup_task__request",
            "pickup_task__request__restaurant",
            "pickup_task__ngo_request",
            "pickup_task__ngo_request__accepted_by",
        )
        .order_by("-created_at")
    )


def mark_delivery_awaiting_transparency_proof(pickup_task):
    pickup_task.transparency_status = PickupTask.TRANSPARENCY_STATUS_AWAITING_PROOF
    pickup_task.save(update_fields=["transparency_status"])
    return pickup_task


def mark_delivery_proof_uploaded(pickup_task):
    pickup_task.transparency_status = PickupTask.TRANSPARENCY_STATUS_PROOF_UPLOADED
    pickup_task.save(update_fields=["transparency_status"])
    return pickup_task
